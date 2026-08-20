"""Declared upstream tool-surface caps: measure, parse, assemble-time enforce."""

from __future__ import annotations

import pytest

from agentcore.core.errors import ValidationError
from agentcore.llm.platform_pool import (
    PlatformPoolMember,
    ToolSurfaceLimits,
    replace_platform_pool_snapshot,
)
from agentcore.llm.tool_surface import (
    TOOL_SURFACE_LIMIT_PREFIX,
    TOOL_SURFACE_LIMIT_SUFFIX,
    ToolSurfaceLimitExceededError,
    check_tool_surface,
    enforce_platform_member_tool_surface,
    format_tool_surface_limit_message,
    measure_tool_surface,
    parse_tool_surface_limits,
    tool_surface_limits_as_dict,
)

_GO = "https://opencode.ai/zen/go/v1"
_CID = "11111111-1111-1111-1111-111111111111"


def _fn(name: str, props: dict | None = None) -> dict:
    parameters: dict = {"type": "object"}
    if props is not None:
        parameters["properties"] = props
    return {
        "type": "function",
        "function": {"name": name, "description": name, "parameters": parameters},
    }


def test_unspecified_limits_are_unlimited():
    limits = parse_tool_surface_limits(None)
    assert limits.is_unrestricted()
    assert parse_tool_surface_limits({}).is_unrestricted()
    assert check_tool_surface([_fn("a"), _fn("b")], limits) == []


def test_parse_rejects_unknown_dimension_and_negatives():
    with pytest.raises(ValidationError, match="未知"):
        parse_tool_surface_limits({"max_tools": 8, "max_tokens": 1})
    with pytest.raises(ValidationError, match="不能为负"):
        parse_tool_surface_limits({"max_tools": -1})
    with pytest.raises(ValidationError, match="须为整数"):
        parse_tool_surface_limits({"max_tools": True})


def test_measure_counts_tools_and_top_level_properties_only():
    tools = [
        _fn("one", {"a": {"type": "string"}, "b": {"type": "object", "properties": {"n": {}}}}),
        _fn("two", {"c": {"type": "string"}}),
        _fn("empty"),
    ]
    measured = measure_tool_surface(tools)
    assert measured["tool_count"] == 3
    # Nested ``n`` is not counted — we do not claim vendor nested semantics.
    assert measured["properties_total"] == 3
    assert measured["properties_per_tool_max"] == 2


def test_max_tools_breach_copy_does_not_promise_trimming():
    limits = ToolSurfaceLimits(max_tools=1)
    tools = [_fn("a"), _fn("b")]
    breaches = check_tool_surface(tools, limits)
    assert breaches == ["工具条数 2，声明上限 1"]
    msg = format_tool_surface_limit_message(breaches)
    assert msg.startswith(TOOL_SURFACE_LIMIT_PREFIX)
    assert msg.endswith(TOOL_SURFACE_LIMIT_SUFFIX)
    assert "自动裁剪" in msg
    assert "16" not in msg  # no hardcoded vendor cap


def test_property_dimensions_are_independent():
    tools = [_fn("wide", {"a": {}, "b": {}, "c": {}})]
    assert check_tool_surface(tools, ToolSurfaceLimits(max_properties_total=2))
    assert check_tool_surface(tools, ToolSurfaceLimits(max_properties_per_tool=2))
    assert check_tool_surface(tools, ToolSurfaceLimits(max_properties_total=3)) == []
    assert check_tool_surface(tools, ToolSurfaceLimits(max_properties_per_tool=3)) == []


def test_compact_dict_omits_unlimited_keys():
    assert tool_surface_limits_as_dict(ToolSurfaceLimits()) == {}
    assert tool_surface_limits_as_dict(ToolSurfaceLimits(max_tools=8)) == {"max_tools": 8}


def test_enforce_skips_when_no_pool_member():
    replace_platform_pool_snapshot(())
    enforce_platform_member_tool_surface(
        [_fn("a"), _fn("b")], api_key="sk-env", base_url=_GO
    )


def test_enforce_raises_and_logs_without_sending_semantics(monkeypatch):
    seen: list[tuple[str, dict]] = []

    def _warn(event: str, **kwargs) -> None:
        seen.append((event, kwargs))

    monkeypatch.setattr("agentcore.llm.tool_surface.logger.warning", _warn)
    replace_platform_pool_snapshot(
        (
            PlatformPoolMember(
                id=_CID,
                label="Go-A",
                api_key="sk-a",
                base_url=_GO,
                subscription_day=18,
                enabled=True,
                tool_surface_limits=ToolSurfaceLimits(max_tools=1),
            ),
        )
    )
    with pytest.raises(ToolSurfaceLimitExceededError, match=TOOL_SURFACE_LIMIT_PREFIX) as ei:
        enforce_platform_member_tool_surface(
            [_fn("a"), _fn("b")], api_key="sk-a", base_url=_GO
        )
    err = ei.value
    assert err.retryable is False
    assert err.details["tool_count"] == 2
    assert err.details["exceeded"] == ["max_tools"]
    assert "platform_credential_id" not in err.details
    assert seen[0][0] == "llm.tool_surface.limit_exceeded"
    assert seen[0][1]["platform_credential_id"] == _CID


@pytest.mark.asyncio
async def test_complete_does_not_post_when_declared_cap_is_exceeded(monkeypatch):
    import httpx

    from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
    from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
    from agentcore.llm.provider.protocol import LLMMessage, LLMRequest

    replace_platform_pool_snapshot(
        (
            PlatformPoolMember(
                id=_CID,
                label="Go-A",
                api_key="sk-a",
                base_url=_GO,
                subscription_day=18,
                enabled=True,
                tool_surface_limits=ToolSurfaceLimits(max_tools=1),
            ),
        )
    )
    posted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(str(request.url))
        raise AssertionError("must not POST when the assembled surface does not fit")

    def factory(**kwargs):
        return httpx.AsyncClient(
            base_url=str(kwargs.get("base_url") or _GO),
            headers=kwargs.get("headers"),
            timeout=kwargs.get("timeout"),
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )

    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible.outbound_async_client",
        factory,
    )
    provider = OpenAICompatibleProvider(name="platform", api_key="sk-a", base_url=_GO)
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        scenario="chat",
        tools=[_fn("a"), _fn("b")],
    )
    try:
        with pytest.raises(ToolSurfaceLimitExceededError, match="不会自动裁剪"):
            await provider.complete(req)
        assert posted == []
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_byok_leaf_does_not_apply_pool_member_caps(monkeypatch):
    import httpx

    from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
    from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
    from agentcore.llm.provider.protocol import LLMMessage, LLMRequest

    replace_platform_pool_snapshot(
        (
            PlatformPoolMember(
                id=_CID,
                label="Go-A",
                api_key="sk-a",
                base_url=_GO,
                subscription_day=18,
                enabled=True,
                tool_surface_limits=ToolSurfaceLimits(max_tools=1),
            ),
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "model": DEEPSEEK_V4_FLASH,
            },
        )

    def factory(**kwargs):
        return httpx.AsyncClient(
            base_url=str(kwargs.get("base_url") or _GO),
            headers=kwargs.get("headers"),
            timeout=kwargs.get("timeout"),
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )

    monkeypatch.setattr(
        "agentcore.llm.provider.openai_compatible.outbound_async_client",
        factory,
    )
    provider = OpenAICompatibleProvider(name="user", api_key="sk-a", base_url=_GO)
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=DEEPSEEK_V4_FLASH,
        scenario="chat",
        tools=[_fn("a"), _fn("b")],
    )
    try:
        result = await provider.complete(req)
        assert result.content == "ok"
    finally:
        await provider.close()
