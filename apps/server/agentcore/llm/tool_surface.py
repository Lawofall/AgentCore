"""Declared upstream tool-surface caps on a platform-pool member.

Ops fill numbers on the credential (same provider can have different
subscription tiers). Unspecified = unlimited. This module only *measures our
assembled OpenAI-format surface* and compares it to that declaration — it does
not invent vendor counting rules, drop tools, or retry at a lower tier.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.errors import LLMError, ValidationError
from agentcore.core.logging import get_logger
from agentcore.llm.platform_pool import ToolSurfaceLimits

logger = get_logger(__name__)

# User-facing copy: honest about the declaration and about what we will not do.
TOOL_SURFACE_LIMIT_PREFIX = "该平台凭据声明的上游工具面上限装不下当前装配的工具面"
TOOL_SURFACE_LIMIT_SUFFIX = "本次不会发给上游，也不会自动裁剪工具。"

_LIMIT_FIELDS = ("max_tools", "max_properties_total", "max_properties_per_tool")


class ToolSurfaceLimitExceededError(LLMError):
    """Assembled tools do not fit this credential's declared upstream caps."""

    retryable = False


def parse_tool_surface_limits(raw: object | None) -> ToolSurfaceLimits:
    """Parse a stored / request object. ``None`` / ``{}`` = unlimited."""
    if raw is None:
        return ToolSurfaceLimits()
    if not isinstance(raw, dict):
        raise ValidationError("上游工具面上限须为对象")
    extra = set(raw) - set(_LIMIT_FIELDS)
    if extra:
        raise ValidationError(f"未知的上游工具面维度：{', '.join(sorted(extra))}")
    return ToolSurfaceLimits(
        max_tools=_optional_limit(raw.get("max_tools"), "max_tools"),
        max_properties_total=_optional_limit(
            raw.get("max_properties_total"), "max_properties_total"
        ),
        max_properties_per_tool=_optional_limit(
            raw.get("max_properties_per_tool"), "max_properties_per_tool"
        ),
    )


def tool_surface_limits_as_dict(limits: ToolSurfaceLimits) -> dict[str, int]:
    """Compact JSONB payload — omitted keys stay unlimited."""
    out: dict[str, int] = {}
    if limits.max_tools is not None:
        out["max_tools"] = limits.max_tools
    if limits.max_properties_total is not None:
        out["max_properties_total"] = limits.max_properties_total
    if limits.max_properties_per_tool is not None:
        out["max_properties_per_tool"] = limits.max_properties_per_tool
    return out


def _optional_limit(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} 须为整数")
    if value < 0:
        raise ValidationError(f"{name} 不能为负")
    return value


def _function_block(tool: dict) -> dict:
    fn = tool.get("function")
    if isinstance(fn, dict):
        return fn
    return tool


def _top_level_property_count(tool: dict) -> int:
    """Count ``function.parameters.properties`` keys — top level only.

    Nested / additionalProperties / items schemas are not counted. Upstream
    may count differently; this is our assembled-surface measurement.
    """
    params = _function_block(tool).get("parameters")
    if not isinstance(params, dict):
        return 0
    props = params.get("properties")
    if not isinstance(props, dict):
        return 0
    return len(props)


def measure_tool_surface(tools: list[dict] | None) -> dict[str, int]:
    """Our measurement of an assembled OpenAI ``tools`` array."""
    defs = tools if isinstance(tools, list) else []
    per_tool = [_top_level_property_count(t) if isinstance(t, dict) else 0 for t in defs]
    return {
        "tool_count": len(defs),
        "properties_total": sum(per_tool),
        "properties_per_tool_max": max(per_tool) if per_tool else 0,
    }


def _breach_lines(measured: dict[str, int], limits: ToolSurfaceLimits) -> list[str]:
    lines: list[str] = []
    if limits.max_tools is not None and measured["tool_count"] > limits.max_tools:
        lines.append(f"工具条数 {measured['tool_count']}，声明上限 {limits.max_tools}")
    if (
        limits.max_properties_total is not None
        and measured["properties_total"] > limits.max_properties_total
    ):
        lines.append(
            f"属性合计 {measured['properties_total']}，声明上限 {limits.max_properties_total}"
        )
    if (
        limits.max_properties_per_tool is not None
        and measured["properties_per_tool_max"] > limits.max_properties_per_tool
    ):
        lines.append(
            f"单工具属性数 {measured['properties_per_tool_max']}，"
            f"声明上限 {limits.max_properties_per_tool}"
        )
    return lines


def format_tool_surface_limit_message(breaches: list[str]) -> str:
    detail = "；".join(breaches)
    return f"{TOOL_SURFACE_LIMIT_PREFIX}（{detail}）。{TOOL_SURFACE_LIMIT_SUFFIX}"


def check_tool_surface(
    tools: list[dict] | None, limits: ToolSurfaceLimits
) -> list[str]:
    """Return breach lines when ``tools`` exceeds ``limits``. Empty = fits."""
    if limits.is_unrestricted():
        return []
    return _breach_lines(measure_tool_surface(tools), limits)


def enforce_platform_member_tool_surface(
    tools: list[Any],
    *,
    api_key: str,
    base_url: str,
) -> None:
    """Raise if the pool member bound to this key declared a cap this surface exceeds.

    No member (env / per-model override / BYOK) → unlimited. Does not walk the
    pool for a roomier member and does not strip tools.
    """
    from agentcore.llm.platform_pool_scheduler import member_for_credentials

    member = member_for_credentials(api_key, base_url)
    if member is None:
        return
    limits = member.tool_surface_limits
    breaches = check_tool_surface(tools if isinstance(tools, list) else [], limits)
    if not breaches:
        return
    measured = measure_tool_surface(tools if isinstance(tools, list) else [])
    exceeded: list[str] = []
    if limits.max_tools is not None and measured["tool_count"] > limits.max_tools:
        exceeded.append("max_tools")
    if (
        limits.max_properties_total is not None
        and measured["properties_total"] > limits.max_properties_total
    ):
        exceeded.append("max_properties_total")
    if (
        limits.max_properties_per_tool is not None
        and measured["properties_per_tool_max"] > limits.max_properties_per_tool
    ):
        exceeded.append("max_properties_per_tool")
    logger.warning(
        "llm.tool_surface.limit_exceeded",
        platform_credential_id=member.id,
        tool_count=measured["tool_count"],
        properties_total=measured["properties_total"],
        properties_per_tool_max=measured["properties_per_tool_max"],
        max_tools=limits.max_tools,
        max_properties_total=limits.max_properties_total,
        max_properties_per_tool=limits.max_properties_per_tool,
        exceeded=exceeded,
    )
    raise ToolSurfaceLimitExceededError(
        format_tool_surface_limit_message(breaches),
        tool_count=measured["tool_count"],
        properties_total=measured["properties_total"],
        properties_per_tool_max=measured["properties_per_tool_max"],
        exceeded=exceeded,
        declared=tool_surface_limits_as_dict(limits),
    )
