"""Send-code reputation limits — independent of AuthRateLimitMiddleware.

Protects the outbound-mail domain (cooldown / daily / per-IP hourly), not
sign-up economics. Checked before a message is handed to the mailer.
"""

from __future__ import annotations

import math

from agentcore.config import settings
from agentcore.core.errors import RateLimitedError
from agentcore.core.rate_limit import RateLimiter
from agentcore.middleware.rate_limit import (
    email_send_cooldown_limiter,
    email_send_daily_limiter,
    email_send_ip_limiter,
)


def enforce_email_send_rate_limit(*, email: str, ip: str) -> None:
    """Raise :class:`RateLimitedError` when this inbox or IP is sending too fast.

    Order: IP hourly → inbox daily → inbox cooldown. A blocked check is not
    recorded (sliding window), so a 429 does not burn the remaining buckets.
    """
    if not settings.rate_limit_enabled:
        return
    checks: list[tuple[RateLimiter, str, str]] = [
        (email_send_ip_limiter, f"ip:{ip or 'unknown'}", "发送过于频繁，请稍后再试"),
        (
            email_send_daily_limiter,
            f"day:{email}",
            "今日验证码次数已用完，请明天再试",
        ),
        (
            email_send_cooldown_limiter,
            f"cd:{email}",
            "验证码发送过于频繁，请稍后再试",
        ),
    ]
    for limiter, key, message in checks:
        decision = limiter.check(key)
        if not decision.allowed:
            retry_after = max(1, math.ceil(decision.retry_after))
            raise RateLimitedError(
                message,
                retry_after=float(retry_after),
            )
