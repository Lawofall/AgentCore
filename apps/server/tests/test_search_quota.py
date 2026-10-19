"""Platform search-count decisions. No database."""

from datetime import UTC, datetime

import pytest

from agentcore.core.errors import QuotaExceededError
from agentcore.search.connection import (
    normalize_search_base_url,
    normalize_search_label,
)
from agentcore.tools.builtin.web.search_quota import SearchQuotaLimits, search_quota_block

NOW = datetime(2026, 9, 15, 12, tzinfo=UTC)


def test_search_quota_blocks_the_exhausted_window():
    daily = search_quota_block(
        SearchQuotaLimits(daily=40, monthly=400),
        daily_used=40,
        monthly_used=40,
        now=NOW,
    )
    assert isinstance(daily, QuotaExceededError)
    assert daily.dimension == "search_daily"
    assert "今日平台搜索次数" in str(daily)
    assert "设置 · 联网搜索" in str(daily)

    monthly = search_quota_block(
        SearchQuotaLimits(daily=0, monthly=400),
        daily_used=0,
        monthly_used=400,
        now=NOW,
    )
    assert isinstance(monthly, QuotaExceededError)
    assert monthly.dimension == "search_monthly"


def test_search_quota_unlimited_windows_do_not_block():
    assert (
        search_quota_block(
            SearchQuotaLimits(daily=0, monthly=0),
            daily_used=999,
            monthly_used=999,
            now=NOW,
        )
        is None
    )
    assert (
        search_quota_block(
            SearchQuotaLimits(daily=40, monthly=400),
            daily_used=39,
            monthly_used=399,
            now=NOW,
        )
        is None
    )


def test_normalize_search_address_and_label():
    assert normalize_search_base_url("deepseek", "") == "https://api.deepseek.com"
    assert normalize_search_label("deepseek", "") == "DeepSeek"
    with pytest.raises(Exception, match="不支持"):
        normalize_search_base_url("searxng", "https://search.example")
    with pytest.raises(Exception, match="不支持"):
        normalize_search_label("searxng", "")
