"""每条用户面错误文案指向的出口，必须真的存在；且不得点名客户端页面。

两个反复踩的坑，各自烧掉过一批用户的时间：

1. **点名客户端页面**——同一句要发给桌面、手机、admin，而各端 Key / 模型入口
   名字不同（桌面「设置 · 服务商」/「设置 · 模型」，手机只有「模型配置」）。
   句子里写任何一个页名，都会在另外两端指向不存在的页。导航由各端 CTA 负责。
2. **「点重试」/「点击重试」**——红错误卡按定案 A 不挂重试入口
   （``AssistantMessage.tsx``），最接近的「重新生成」还会截断后续历史。点名一个
   不存在的按钮，等于让人在屏幕上白找。「请稍后再试」= 重发本条，没有这个问题。

新增用户面文案时把它挂进 ``_USER_FACING_COPY`` 即可，这两条不必再各写一遍。
经 AI 复述到用户的**静态**共享系统提示同样挂进来（例如凭据卫生）。运行时拼装的大模板
不要硬塞；``product_help*`` 是导航技能、按端分文案由
``test_product_help_manual_links`` 守，也不在此列。
"""

from __future__ import annotations

import pytest

from agentcore.conversation.quota import _BYOK_EXIT
from agentcore.core.errors import (
    BYOK_KEY_REQUIRED_MESSAGE,
    MAX_RETRY_AFTER,
    RETRY_AFTER_FROM_HEADER,
    InferenceTokenExpiredError,
    LLMAuthError,
    LLMInsufficientBalanceError,
    LLMKeyRequiredError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    upstream_rate_limit_error,
)
from agentcore.llm.errors import (
    OPENCODE_CREDITS_MESSAGE,
    OPENCODE_FREE_USAGE_MESSAGE,
    OPENCODE_GO_QUOTA_MESSAGE,
    OPENCODE_MODEL_UNAVAILABLE_MESSAGE,
    OPENCODE_MONTHLY_LIMIT_MESSAGE,
    OPENCODE_PLATFORM_MODEL_MESSAGE,
    OPENCODE_PLATFORM_USAGE_MESSAGE,
    OPENCODE_REGION_BYOK_MESSAGE,
    OPENCODE_REGION_PLATFORM_MESSAGE,
    OPENCODE_USER_LIMIT_MESSAGE,
    opencode_region_product_message,
)
from agentcore.llm.factory import _MISSING_LLM_CREDENTIALS_USER_MESSAGE
from agentcore.llm.tools_gate import (
    TOOLS_SOFT_GATE_WARNING,
    TOOLS_UNAVAILABLE_RUNTIME_MESSAGE,
)
from agentcore.runtime.resolve.prompt import (
    _CEO_CORE_HINT,
    _DEFAULT_SYSTEM_PROMPT,
)

# 服务端共享句不得点名任何一端的页。桌面有「模型」「服务商」两页，手机只有「模型配置」。
_CLIENT_PAGE_NAMES = (
    "设置 · 服务商",
    "设置·服务商",
    "设置 · 模型配置",
    "设置·模型配置",
    "设置 · 模型",
    "设置·模型",
    "模型配置",
)

_USER_FACING_COPY: dict[str, str] = {
    "byok_key_required": BYOK_KEY_REQUIRED_MESSAGE,
    "llm_key_required": LLMKeyRequiredError().message,
    "quota_exceeded": LLMQuotaExceededError().message,
    "quota_byok_exit": _BYOK_EXIT,
    "auth_byok": LLMAuthError(provider_name="user").message,
    "auth_platform": LLMAuthError(provider_name="platform").message,
    "balance_byok": LLMInsufficientBalanceError(provider_name="user").message,
    "balance_platform": LLMInsufficientBalanceError(provider_name="platform").message,
    "balance_opencode_credits": OPENCODE_CREDITS_MESSAGE,
    "opencode_go_quota": OPENCODE_GO_QUOTA_MESSAGE,
    "opencode_free_usage": OPENCODE_FREE_USAGE_MESSAGE,
    "opencode_monthly_limit": OPENCODE_MONTHLY_LIMIT_MESSAGE,
    "opencode_user_limit": OPENCODE_USER_LIMIT_MESSAGE,
    "opencode_model_unavailable": OPENCODE_MODEL_UNAVAILABLE_MESSAGE,
    "opencode_platform_usage": OPENCODE_PLATFORM_USAGE_MESSAGE,
    "opencode_platform_model": OPENCODE_PLATFORM_MODEL_MESSAGE,
    "opencode_region_byok": opencode_region_product_message(
        b'{"error":{"type":"RegionError","message":'
        b'"opt in: https://opencode.ai/workspace/wrk_test/go"}}',
        platform=False,
    ),
    "opencode_region_byok_stem": OPENCODE_REGION_BYOK_MESSAGE,
    "opencode_region_platform": OPENCODE_REGION_PLATFORM_MESSAGE,
    "inference_token_expired": InferenceTokenExpiredError().message,
    "rate_limit_unknown_cooldown": LLMRateLimitError().message,
    "rate_limit_short_cooldown": LLMRateLimitError(retry_after=12).message,
    "rate_limit_short_cooldown_attested": LLMRateLimitError(
        retry_after=12, retry_after_source=RETRY_AFTER_FROM_HEADER
    ).message,
    "rate_limit_day_reset_byok": upstream_rate_limit_error(
        59760.0, credential_source="user"
    ).message,
    "rate_limit_day_reset_platform": upstream_rate_limit_error(
        59760.0, credential_source="platform"
    ).message,
    "rate_limit_at_ceiling": upstream_rate_limit_error(MAX_RETRY_AFTER).message,
    "missing_credentials": _MISSING_LLM_CREDENTIALS_USER_MESSAGE,
    "tools_soft_gate": TOOLS_SOFT_GATE_WARNING,
    "tools_unavailable": TOOLS_UNAVAILABLE_RUNTIME_MESSAGE,
    "shared_system_prompt_base": _DEFAULT_SYSTEM_PROMPT,
    "ceo_core_hint": _CEO_CORE_HINT,
}


@pytest.mark.parametrize("name", sorted(_USER_FACING_COPY))
def test_copy_never_names_a_client_page(name):
    copy = _USER_FACING_COPY[name]
    for page in _CLIENT_PAGE_NAMES:
        assert page not in copy


@pytest.mark.parametrize("name", sorted(_USER_FACING_COPY))
def test_copy_never_tells_the_user_to_press_a_retry_button(name):
    copy = _USER_FACING_COPY[name]
    assert "点重试" not in copy
    assert "点击重试" not in copy


def test_key_required_copy_is_single_sourced_across_leaf_and_preflight():
    """曾经同一句话散在四处，改页名要改四遍——改一遍就漏一处。"""
    from agentcore.api.routes.conversations import _helpers
    from agentcore.api.routes.inference import proxy

    assert LLMKeyRequiredError().message == BYOK_KEY_REQUIRED_MESSAGE
    assert _helpers.BYOK_KEY_REQUIRED_MESSAGE is BYOK_KEY_REQUIRED_MESSAGE
    assert proxy.BYOK_KEY_REQUIRED_MESSAGE is BYOK_KEY_REQUIRED_MESSAGE


def test_key_related_copy_names_the_remedy_not_a_page():
    """Key 出口是接入/更新 API Key；换模型出口是更换模型。页名留给各端 CTA。"""
    assert "API Key" in BYOK_KEY_REQUIRED_MESSAGE
    assert "API Key" in LLMAuthError(provider_name="user").message
    assert "更换" in TOOLS_UNAVAILABLE_RUNTIME_MESSAGE
    assert "模型" in TOOLS_UNAVAILABLE_RUNTIME_MESSAGE
    assert "改选" in _MISSING_LLM_CREDENTIALS_USER_MESSAGE
    assert "模型" in _MISSING_LLM_CREDENTIALS_USER_MESSAGE
    for copy in (
        BYOK_KEY_REQUIRED_MESSAGE,
        LLMAuthError(provider_name="user").message,
        TOOLS_UNAVAILABLE_RUNTIME_MESSAGE,
        _MISSING_LLM_CREDENTIALS_USER_MESSAGE,
    ):
        assert "设置" not in copy


def test_platform_region_copy_does_not_leak_workspace():
    for copy in (
        OPENCODE_REGION_PLATFORM_MESSAGE,
        OPENCODE_PLATFORM_USAGE_MESSAGE,
        OPENCODE_PLATFORM_MODEL_MESSAGE,
    ):
        assert "opencode.ai/workspace" not in copy
        assert "wrk_" not in copy
        assert "opencode.ai" not in copy
