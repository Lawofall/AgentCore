"""Unit tests for production startup config validation (agentcore.main).

Focus: the fail-closed guards in ``_validate_production_security``. The byok
master-key guard (安全权限与治理.md §七) is the one that matters most — it turns a
missing/malformed ``ENCRYPTION_KEY`` from a silent "boots green but can't chat"
landmine into a boot refusal, since byok makes a per-user key (and thus at-rest
encryption) mandatory.

P1-1: JWT placeholder secrets are checked even when DEBUG=true — only the
explicit local-dev pair (DEBUG + ALLOW_INSECURE_JWT_SECRET) may keep them.
"""

import pytest

from agentcore.config import settings
from agentcore.main import _validate_jwt_secret, _validate_production_security

# A valid 64-hex (32-byte) AES-256 master key.
_MASTER_KEY = "a" * 64
# A JWT secret that isn't a known placeholder, so the JWT guard passes.
_GOOD_JWT = "x" * 48
_PLACEHOLDER = "dev-secret-change-in-production"


@pytest.fixture
def prod_settings(monkeypatch):
    """Non-debug settings with the JWT + cookie guards already satisfied, so each
    test can isolate one dimension (here: the encryption key)."""
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "jwt_secret_key", _GOOD_JWT)
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", False)
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "cookie_samesite", "none")
    monkeypatch.setattr(settings, "csrf_enabled", True)
    # G5: isolate from host WEB_CONCURRENCY / UVICORN_WORKERS.
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("AGENTCORE_API_WORKERS", raising=False)
    return settings


def test_debug_skips_non_jwt_validation(monkeypatch):
    """DEBUG still bypasses encryption / cloud-RCE guards, but JWT is always checked."""
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "jwt_secret_key", _GOOD_JWT)
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", False)
    monkeypatch.setattr(settings, "billing_mode", "byok")
    monkeypatch.setattr(settings, "encryption_key", "")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("AGENTCORE_API_WORKERS", raising=False)
    _validate_production_security()


def test_placeholder_jwt_refuses_boot_when_debug_without_allow(monkeypatch):
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "jwt_secret_key", _PLACEHOLDER)
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", False)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        _validate_jwt_secret()


def test_placeholder_jwt_refuses_boot_in_production(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "jwt_secret_key", _PLACEHOLDER)
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", True)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        _validate_jwt_secret()


def test_placeholder_jwt_allowed_for_explicit_local_dev(monkeypatch):
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "jwt_secret_key", _PLACEHOLDER)
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", True)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("AGENTCORE_API_WORKERS", raising=False)
    _validate_jwt_secret()
    _validate_production_security()


def test_env_example_alt_placeholder_also_refused(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(
        settings, "jwt_secret_key", "change-this-to-a-random-secret-in-production"
    )
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", False)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        _validate_jwt_secret()


def test_strong_jwt_boots_without_allow_flag(monkeypatch):
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "jwt_secret_key", _GOOD_JWT)
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", False)
    _validate_jwt_secret()


def test_byok_without_master_key_refuses_boot(prod_settings, monkeypatch):
    monkeypatch.setattr(prod_settings, "billing_mode", "byok")
    monkeypatch.setattr(prod_settings, "encryption_key", "")
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY is unset"):
        _validate_production_security()


def test_byok_with_malformed_master_key_refuses_boot(prod_settings, monkeypatch):
    monkeypatch.setattr(prod_settings, "billing_mode", "byok")
    monkeypatch.setattr(prod_settings, "encryption_key", "not-hex")
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY is malformed"):
        _validate_production_security()


def test_byok_with_valid_master_key_boots(prod_settings, monkeypatch):
    monkeypatch.setattr(prod_settings, "billing_mode", "byok")
    monkeypatch.setattr(prod_settings, "encryption_key", _MASTER_KEY)
    _validate_production_security()  # no raise


def test_platform_mode_tolerates_missing_master_key(prod_settings, monkeypatch):
    # Optional credential under platform billing → fail-safe, not fail-closed.
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "encryption_key", "")
    _validate_production_security()  # no raise


def test_platform_mode_malformed_master_key_refuses_boot(prod_settings, monkeypatch):
    # A non-empty but invalid ENCRYPTION_KEY must refuse boot in any billing mode —
    # otherwise BYOK / MFA paths hit binascii 500s at runtime.
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "encryption_key", "not-hex")
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY is malformed"):
        _validate_production_security()


def test_cloud_code_execute_without_ack_refuses_boot(prod_settings, monkeypatch):
    """SEC-005: enabling cloud code_execute (RCE in the API container) without the
    explicit unsafe acknowledgement must fail closed, so a single flag can't expose it."""
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "code_execute_cloud_enabled", True)
    monkeypatch.setattr(prod_settings, "code_execute_cloud_unsafe_ack", False)
    with pytest.raises(RuntimeError, match="CODE_EXECUTE_CLOUD_ENABLED"):
        _validate_production_security()


def test_cloud_code_execute_with_ack_boots(prod_settings, monkeypatch):
    """The deliberate second flag (CODE_EXECUTE_CLOUD_UNSAFE_ACK) lets an operator who
    has a real sandbox in front opt in explicitly."""
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "code_execute_cloud_enabled", True)
    monkeypatch.setattr(prod_settings, "code_execute_cloud_unsafe_ack", True)
    _validate_production_security()  # no raise


def test_cloud_code_execute_default_off_boots(prod_settings, monkeypatch):
    # Default posture: cloud code_execute off → nothing to acknowledge.
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "code_execute_cloud_enabled", False)
    monkeypatch.setattr(prod_settings, "code_execute_cloud_unsafe_ack", False)
    _validate_production_security()  # no raise


def test_debug_skips_cloud_code_execute_guard(monkeypatch):
    # debug bypasses non-JWT production guards, including the cloud-RCE one.
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "jwt_secret_key", _GOOD_JWT)
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", False)
    monkeypatch.setattr(settings, "code_execute_cloud_enabled", True)
    monkeypatch.setattr(settings, "code_execute_cloud_unsafe_ack", False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("AGENTCORE_API_WORKERS", raising=False)
    _validate_production_security()  # no raise


def test_cors_wildcard_refuses_boot_in_production(prod_settings, monkeypatch):
    """Credentialed CORS + origin '*' is rejected by browsers — refuse production boot."""
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "cors_allow_origins", "*")
    with pytest.raises(RuntimeError, match="CORS_ALLOW_ORIGINS"):
        _validate_production_security()


def test_cors_wildcard_among_list_refuses_boot(prod_settings, monkeypatch):
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(
        prod_settings, "cors_allow_origins", "http://localhost:5173,*,https://app.example"
    )
    with pytest.raises(RuntimeError, match="CORS_ALLOW_ORIGINS"):
        _validate_production_security()


def test_cors_wildcard_warns_in_debug(monkeypatch):
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "jwt_secret_key", _GOOD_JWT)
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", False)
    monkeypatch.setattr(settings, "cors_allow_origins", "*")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("AGENTCORE_API_WORKERS", raising=False)
    monkeypatch.setattr("agentcore.main.get_logger", lambda _name: spy)
    _validate_production_security()  # no raise
    detail = spy.get("security.cors_wildcard_credentials")
    assert "credentials" in detail["detail"].lower()


def test_csrf_disabled_warns_but_boots(prod_settings, monkeypatch):
    """CSRF_ENABLED=false silently removes the only defense for cookie-session
    clients, so boot has to say so out loud — but it still boots: the only party this
    ever stopped was a self-hoster mid-deploy, for whom a dead server is worse."""
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "csrf_enabled", False)
    monkeypatch.setattr("agentcore.main.logger", spy)

    _validate_production_security()  # no raise

    assert "CSRF_ENABLED=true" in spy.get("security.csrf_disabled")["detail"]


def test_debug_skips_csrf_guard(monkeypatch):
    # Local dev may turn CSRF off (e.g. hand-driven curl); only a served server must not.
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "jwt_secret_key", _GOOD_JWT)
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", False)
    monkeypatch.setattr(settings, "csrf_enabled", False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("AGENTCORE_API_WORKERS", raising=False)
    _validate_production_security()  # no raise


def test_cors_explicit_origins_boots(prod_settings, monkeypatch):
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(
        prod_settings,
        "cors_allow_origins",
        "http://localhost:5173,https://app.example",
    )
    _validate_production_security()  # no raise


# --- 单进程假设：多 worker 一律拒启（跟播 / 履约中枢无跨进程扇出）------------------


def test_multi_worker_memory_rate_limit_refuses_boot_in_production(
    prod_settings, monkeypatch
):
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "rate_limit_backend", "memory")
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with pytest.raises(RuntimeError, match="cross-process"):
        _validate_production_security()


def test_multi_worker_refuses_boot_even_with_redis_rate_limit(
    prod_settings, monkeypatch
):
    """redis 只共享限流器：履约中枢与对话事件流仍是进程内单例。

    这条守的是**静默**失效——跟播落到没跑该回合的 worker 上只会一直心跳，客户端
    分辨不出它和「对话真空闲」的区别，所以宁可启动就拒，不放行等它悄悄坏。
    """
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "rate_limit_backend", "redis")
    monkeypatch.setenv("AGENTCORE_API_WORKERS", "2")
    with pytest.raises(RuntimeError, match="cross-process"):
        _validate_production_security()


def test_multi_worker_warns_in_debug(monkeypatch):
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "jwt_secret_key", _GOOD_JWT)
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", False)
    monkeypatch.setattr(settings, "rate_limit_backend", "memory")
    monkeypatch.setenv("UVICORN_WORKERS", "3")
    monkeypatch.setattr("agentcore.main.get_logger", lambda _name: spy)
    _validate_production_security()  # no raise
    detail = spy.get("deploy.multi_worker_refused")
    assert detail["workers"] == 3
    assert detail["rate_limit_backend"] == "memory"


def test_single_worker_memory_boots_in_production(prod_settings, monkeypatch):
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "rate_limit_backend", "memory")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("AGENTCORE_API_WORKERS", raising=False)
    _validate_production_security()  # no raise


def test_rate_limit_backend_rejects_unknown_value():
    from pydantic import ValidationError

    from agentcore.config.auth import AuthSettings

    with pytest.raises(ValidationError):
        AuthSettings(rate_limit_backend="memcache")


# --- SMTP + open registration: boot warning only (send path unchanged) ----------


def test_smtp_unconfigured_warns_when_registration_open(prod_settings, monkeypatch):
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "registration_open", True)
    monkeypatch.setattr(prod_settings, "smtp_host", "")
    monkeypatch.setattr(prod_settings, "smtp_from_address", "")
    monkeypatch.setattr("agentcore.main.get_logger", lambda _name: spy)

    _validate_production_security()  # no raise

    detail = spy.get("email.smtp_unconfigured_registration")["detail"]
    assert "REGISTRATION_OPEN=true" in detail
    assert "SMTP_HOST" in detail
    assert "/v1/auth/register/send-code" in detail


def test_smtp_unconfigured_silent_in_debug(monkeypatch):
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "jwt_secret_key", _GOOD_JWT)
    monkeypatch.setattr(settings, "allow_insecure_jwt_secret", False)
    monkeypatch.setattr(settings, "registration_open", True)
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_from_address", "")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    monkeypatch.delenv("AGENTCORE_API_WORKERS", raising=False)
    monkeypatch.setattr("agentcore.main.get_logger", lambda _name: spy)

    _validate_production_security()  # no raise

    assert not any(
        name == "email.smtp_unconfigured_registration" for name, _ in spy.events
    )


def test_smtp_unconfigured_silent_when_registration_closed(prod_settings, monkeypatch):
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "registration_open", False)
    monkeypatch.setattr(prod_settings, "smtp_host", "")
    monkeypatch.setattr(prod_settings, "smtp_from_address", "")
    monkeypatch.setattr("agentcore.main.get_logger", lambda _name: spy)

    _validate_production_security()  # no raise

    assert not any(
        name == "email.smtp_unconfigured_registration" for name, _ in spy.events
    )


def test_smtp_configured_silent_when_registration_open(prod_settings, monkeypatch):
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(prod_settings, "billing_mode", "platform")
    monkeypatch.setattr(prod_settings, "registration_open", True)
    monkeypatch.setattr(prod_settings, "smtp_host", "smtp.test.local")
    monkeypatch.setattr(prod_settings, "smtp_from_address", "noreply@example.com")
    monkeypatch.setattr("agentcore.main.get_logger", lambda _name: spy)

    _validate_production_security()  # no raise

    assert not any(
        name == "email.smtp_unconfigured_registration" for name, _ in spy.events
    )
