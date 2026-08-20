"""Product face of an upstream 429, split by cooldown length and who funds the key.

The engine already refuses to retry past ``MAX_RETRY_AFTER`` (``_rate_limit_should_retry``);
these pin that the *error object* says the same thing — no「请稍后再试」on a cooldown
nobody will wait out — and that the exit offered matches the payer.

They also pin that no branch names a「重试」button: the red error card has none
(定案 A), and the settings exit is「服务商」, the page keys actually live on.

And that no branch puts a clock time in the sentence: where upstream declared one, the
recovery moment travels as an ISO-8601 UTC instant for the client to render in the
reader's own timezone, and where it did not, nothing is dated at all. The copy stays
true either way (a UTC wall clock read literally cost a China user a whole day of
waiting for an allowance that returned at local midnight).
"""

from datetime import UTC, datetime

import httpx
import pytest

from agentcore.core.error_codes import ErrorCode
from agentcore.core.errors import (
    MAX_RETRY_AFTER,
    RETRY_AFTER_FROM_HEADER,
    LLMError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    recovery_at_iso,
    upstream_rate_limit_error,
)
from agentcore.llm.errors import error_context_from
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.openai_compatible import (
    _MAX_RETRY_AFTER,
    OpenAICompatibleProvider,
    _rate_limit_should_retry,
)
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest

# Longest cooldown seen in production: an upstream UTC day reset (16.6h).
_DAY_RESET = 59760.0
_NOW = datetime(2026, 8, 13, 8, 48, tzinfo=UTC)
_RECOVERY_AT = "2026-08-14T01:24:00Z"


def _declared_429(retry_after: float, **kwargs) -> LLMError:
    """A 429 whose cooldown upstream actually stated in a ``Retry-After`` header.

    Only a declared cooldown may date a recovery (see ``RETRY_AFTER_FROM_HEADER``),
    so the moment-bearing branches all start here; the unattested ones are pinned
    separately in :func:`test_an_undeclared_cooldown_dates_nothing`.
    """
    return upstream_rate_limit_error(
        retry_after, now=_NOW, retry_after_source=RETRY_AFTER_FROM_HEADER, **kwargs
    )


def test_rate_limit_error_zh_message_short_retry_unattested():
    """未 attested 的短冷却：文案不报秒数，线上也不带 retry_after。"""
    e = LLMRateLimitError(retry_after=12)
    assert e.code == ErrorCode.LLM_RATE_LIMIT
    assert e.message == "上游限流，暂时无法继续本回合。请稍后再试。"
    assert "12" not in e.message
    ctx = error_context_from(e)
    assert ctx is None or ctx.get("retry_after") is None


def test_rate_limit_error_zh_message_short_retry_attested():
    e = LLMRateLimitError(retry_after=12, retry_after_source=RETRY_AFTER_FROM_HEADER)
    assert e.code == ErrorCode.LLM_RATE_LIMIT
    assert "上游限流" in e.message
    assert "12" in e.message
    ctx = error_context_from(e)
    assert ctx is not None
    assert ctx.get("retry_after") == 12.0


def test_rate_limit_error_zh_message_long_retry_no_hour_promise():
    e = LLMRateLimitError(retry_after=3600)
    assert "上游限流" in e.message
    assert "3600" not in e.message
    assert "一小时" not in e.message
    assert e.retry_after == 3600.0
    ctx = error_context_from(e)
    assert ctx is None or ctx.get("retry_after") is None


# ---- single source of the ceiling -------------------------------------------


def test_max_retry_after_is_single_sourced():
    """One constant decides both「引擎重不重试」and「文案怎么说」—— no second 30."""
    assert _MAX_RETRY_AFTER is MAX_RETRY_AFTER
    # The provider protocol层 deliberately does not re-declare it (it would drift
    # from the copy that quotes the same ceiling).
    from agentcore.llm.provider import protocol

    assert not hasattr(protocol, "MAX_RETRY_AFTER")


@pytest.mark.parametrize(
    "retry_after",
    [None, 0.0, 1.0, MAX_RETRY_AFTER, MAX_RETRY_AFTER + 0.1, 3600.0, _DAY_RESET],
)
@pytest.mark.parametrize("source", [None, "user", "platform"])
def test_error_retryable_agrees_with_engine_decision(retry_after, source):
    """The whole point: the object never advertises a retry the loop already refused."""
    err = upstream_rate_limit_error(retry_after, credential_source=source)
    assert err.retryable is _rate_limit_should_retry(retry_after)


# ---- threshold boundary ------------------------------------------------------


@pytest.mark.parametrize("source", [None, "user", "platform"])
def test_exactly_at_ceiling_keeps_the_retryable_seconds_copy(source):
    err = _declared_429(MAX_RETRY_AFTER, credential_source=source)
    assert isinstance(err, LLMRateLimitError)
    assert err.code == ErrorCode.LLM_RATE_LIMIT
    assert err.retryable is True
    assert err.message == "上游限流，暂时无法继续本回合。请约 30 秒后再试。"
    assert "点重试" not in err.message


@pytest.mark.parametrize("source", [None, "user", "platform"])
def test_exactly_at_ceiling_unattested_does_not_invent_seconds(source):
    err = upstream_rate_limit_error(MAX_RETRY_AFTER, credential_source=source, now=_NOW)
    assert isinstance(err, LLMRateLimitError)
    assert err.retryable is True
    assert err.message == "上游限流，暂时无法继续本回合。请稍后再试。"
    assert "30" not in err.message


@pytest.mark.parametrize("source", [None, "user", "platform"])
def test_just_past_ceiling_stops_promising_a_retry(source):
    err = _declared_429(MAX_RETRY_AFTER + 0.1, credential_source=source)
    assert err.retryable is False
    # The copy the user obeyed 2–4 times before: gone on every branch.
    assert "请稍后再试" not in err.message
    assert "点重试" not in err.message
    # The moment left the sentence on every branch and rides structured instead.
    assert "UTC" not in err.message
    assert err.details["recovery_at"] == recovery_at_iso(MAX_RETRY_AFTER + 0.1, now=_NOW)


# ---- the three branches past the ceiling ------------------------------------


def test_platform_day_reset_takes_the_quota_face():
    """Operator-funded allowance wall: reuse QUOTA_EXCEEDED so the client drops the
    retry button and offers the BYOK exit — no new code, no new CTA."""
    err = _declared_429(_DAY_RESET, credential_source="platform", upstream_status=429)
    assert isinstance(err, LLMQuotaExceededError)
    assert err.code == ErrorCode.QUOTA_EXCEEDED
    assert err.retryable is False
    assert err.message == (
        "平台模型额度已用完，本回合无法继续。请等待上游额度恢复，"
        "或接入自己的 API Key 立即继续。"
    )
    assert "设置" not in err.message
    ctx = error_context_from(err)
    assert ctx is not None
    assert ctx["credential_source"] == "platform"
    assert ctx["retry_after"] == _DAY_RESET
    assert ctx["recovery_at"] == _RECOVERY_AT


def test_byok_day_reset_keeps_the_rate_limit_face_without_a_key_cta():
    """Telling a user who already brought their own key to bring one is nonsense."""
    err = _declared_429(_DAY_RESET, credential_source="user")
    assert isinstance(err, LLMRateLimitError)
    assert err.code == ErrorCode.LLM_RATE_LIMIT
    assert err.retryable is False
    assert err.message == "上游限流，本回合无法继续。你的服务商额度恢复前重试仍会失败。"
    assert "API Key" not in err.message
    ctx = error_context_from(err)
    assert ctx is not None
    assert ctx["credential_source"] == "user"
    assert ctx["recovery_at"] == _RECOVERY_AT


def test_unknown_source_day_reset_takes_the_conservative_branch():
    """Unknown payer: rate-limit face, no retry, and no BYOK CTA guessed into it."""
    err = _declared_429(_DAY_RESET, credential_source=None)
    assert isinstance(err, LLMRateLimitError)
    assert err.code == ErrorCode.LLM_RATE_LIMIT
    assert err.retryable is False
    assert err.message == "上游限流，本回合无法继续。上游额度恢复前重试仍会失败。"
    assert "API Key" not in err.message
    assert "credential_source" not in err.details


def test_recovery_at_is_structured_never_a_duration_nor_a_utc_clock():
    """The one number a user can act on, and the one shape he can act on it in.

    「等 16.6 小时」was never a promise the retry budget made; a pre-worded UTC clock
    was a promise in the wrong timezone. So: no duration in the copy, no clock in the
    copy, and the instant itself on the envelope for the client to localise.
    """
    assert recovery_at_iso(_DAY_RESET, now=_NOW) == _RECOVERY_AT
    for err in (
        _declared_429(_DAY_RESET, credential_source="platform"),
        _declared_429(_DAY_RESET, credential_source="user"),
        _declared_429(_DAY_RESET, credential_source=None),
    ):
        assert err.details["recovery_at"] == _RECOVERY_AT
        assert "16.6" not in err.message
        assert "小时" not in err.message
        assert "UTC" not in err.message
        assert "01:24" not in err.message


@pytest.mark.parametrize("source", [None, "user", "platform"])
def test_an_undeclared_cooldown_dates_nothing(source):
    """The other half of the same rule, now that the moment rides structured.

    Dropping the clock from the copy makes ``recovery_at`` the only place a
    recovery time is stated — so a cooldown upstream never declared must leave it
    empty rather than let the client localise our own backoff into a confident
    「恢复于 X」the vendor never said.
    """
    err = upstream_rate_limit_error(_DAY_RESET, credential_source=source, now=_NOW)
    assert err.retryable is False
    assert "recovery_at" not in err.details
    assert "UTC" not in err.message
    assert "恢复" not in err.message


def test_short_cooldown_never_reaches_the_quota_face():
    """Inside the ceiling a platform 429 is an ordinary retryable throttle."""
    err = _declared_429(5.0, credential_source="platform")
    assert isinstance(err, LLMRateLimitError)
    assert err.retryable is True
    assert "5 秒" in err.message


# ---- provider seam: the 429 raise site carries the credential source ---------


async def _mock_provider(handler, *, name: str, base_url: str) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(name=name, api_key="k", base_url=base_url)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url=base_url, transport=httpx.MockTransport(handler)
    )
    return provider


def _day_reset_429(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        429,
        headers={"retry-after": str(int(_DAY_RESET))},
        content=b'{"error":"rate_limited"}',
    )


def _headerless_429(request: httpx.Request) -> httpx.Response:
    """The production shape behind the 138 give-ups: a 429 that states nothing."""
    return httpx.Response(429, content=b'{"error":"rate_limited"}')


async def _no_sleep(seconds: float) -> None:
    """Run the 2→4→8→16 backoff chain without spending 30 real seconds on it."""


def _req() -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        scenario="title",
    )


async def test_provider_429_on_platform_key_raises_the_quota_face():
    provider = await _mock_provider(
        _day_reset_429, name="platform", base_url="http://example.invalid/v1"
    )
    try:
        with pytest.raises(LLMQuotaExceededError) as ei:
            await provider.complete(_req())
        assert ei.value.retryable is False
        assert ei.value.details["credential_source"] == "platform"
        assert ei.value.details["recovery_at"].endswith("Z")
    finally:
        await provider.close()


async def test_a_headerless_429_on_a_platform_key_dates_nothing(monkeypatch):
    """生产那 138 条走完整条链路：上游一个字没说，用户面就不许出现一个恢复时刻。

    这条走的是 provider 真路径而非直接构造，因为编造发生在接缝上：``_parse_retry_after``
    在无头 429 上兜底成我们自己的退避值，第五次的 32 秒越过上限被放弃，而下游只看见一个
    「32」——照旧算出「上游将于 17:44 恢复」这种精确到分钟的时刻，外加一句同样没有根据的
    「额度已用完」（无头 429 也可能只是限速）。留下的只能是已知事实 + BYOK 出口：接自己的
    key 确实能立刻绕过限流。
    """
    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible.asyncio.sleep",
        _no_sleep,
    )
    provider = await _mock_provider(
        _headerless_429, name="platform", base_url="http://example.invalid/v1"
    )
    try:
        with pytest.raises(LLMRateLimitError) as ei:
            await provider.complete(_req())
        err = ei.value
        # 无从证明是额度用尽，就不摆那张额度墙的脸。
        assert not isinstance(err, LLMQuotaExceededError)
        assert err.code == ErrorCode.LLM_RATE_LIMIT
        assert err.retryable is False
        assert err.details["credential_source"] == "platform"
        # 核心：没有时刻可发——连客户端都没法本地化出一个我们编的钟点。
        assert "recovery_at" not in err.details
        assert error_context_from(err).get("recovery_at") is None
        # 也不猜「额度用完」，不承诺恢复。
        assert "额度" not in err.message
        assert "恢复" not in err.message
        # 保留的那个入口：接自己的 key 是真能立即继续的。
        assert "接入自己的 API Key" in err.message
        assert "设置" not in err.message
    finally:
        await provider.close()


async def test_provider_429_on_byok_key_raises_a_non_retryable_rate_limit():
    provider = await _mock_provider(
        _day_reset_429, name="deepseek", base_url="http://example.invalid/v1"
    )
    try:
        with pytest.raises(LLMRateLimitError) as ei:
            await provider.complete(_req())
        assert ei.value.retryable is False
        assert ei.value.retry_after == _DAY_RESET
        assert ei.value.details["credential_source"] == "user"
        assert "你的服务商额度" in ei.value.message
    finally:
        await provider.close()


async def test_provider_429_on_inference_hop_stays_source_agnostic():
    """The sidecar carrier cannot know the payer — guessing would brand a BYOK wall
    as a platform one, so the hop takes the conservative face."""
    provider = await _mock_provider(
        _day_reset_429, name="platform", base_url="http://example.invalid/inference/v1"
    )
    try:
        with pytest.raises(LLMRateLimitError) as ei:
            await provider.complete(_req())
        assert ei.value.retryable is False
        assert "credential_source" not in ei.value.details
        assert "上游额度" in ei.value.message
    finally:
        await provider.close()
