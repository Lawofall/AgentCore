"""Send-code reputation limiter (independent of auth IP middleware)."""

import pytest

from agentcore.auth.email_rate_limit import enforce_email_send_rate_limit
from agentcore.config import settings
from agentcore.core.errors import RateLimitedError
from agentcore.middleware.rate_limit import reset_rate_limit_state


@pytest.fixture(autouse=True)
def _reset_and_enable(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    reset_rate_limit_state()
    yield
    reset_rate_limit_state()


def test_email_cooldown_blocks_second_send():
    enforce_email_send_rate_limit(email="a@example.com", ip="1.1.1.1")
    with pytest.raises(RateLimitedError, match="过于频繁"):
        enforce_email_send_rate_limit(email="a@example.com", ip="1.1.1.1")


def test_email_keys_are_independent():
    enforce_email_send_rate_limit(email="a@example.com", ip="1.1.1.1")
    enforce_email_send_rate_limit(email="b@example.com", ip="1.1.1.1")


def test_ip_hourly_cap():
    # Limiters are sized at import (default 20/hour). 21st distinct inbox on
    # one IP must 429; cooldown/daily keys differ so only the IP bucket trips.
    reset_rate_limit_state()
    for i in range(settings.email_send_ip_hourly_max):
        enforce_email_send_rate_limit(email=f"u{i}@example.com", ip="9.9.9.9")
    with pytest.raises(RateLimitedError):
        enforce_email_send_rate_limit(email="overflow@example.com", ip="9.9.9.9")


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    for _ in range(5):
        enforce_email_send_rate_limit(email="a@example.com", ip="1.1.1.1")
