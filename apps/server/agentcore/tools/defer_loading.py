"""Vendor deferred loading — infrequent opening-table tools.

The assembled OpenAI-format ``tools[]`` stays frozen and complete. ``consult``
never promotes schemas and never rewrites that table.

When the *wire* speaks native tool-search (official OpenAI ``/responses`` or
official Anthropic ``/messages``), converters stamp ``defer_loading`` on this
roster and inject the vendor search tool. Completions (DeepSeek and anything
else on that path) and unconfirmed relays (OpenCode) never stamp — the model
sees the full opening table.
"""

from __future__ import annotations

from typing import Literal

from agentcore.tools.on_demand import is_mcp_tool_name

# Infrequent factory names. MCP actions ride the same stamp via prefix.
# Core loop tools (read / write / edit / grep / glob / file_list / run /
# ask_user / delegate / consult / replan / cancel_worker / web_* / host /
# browser / file_delete / file_batch) are never in this set.
DEFERRED_OPENING_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "md_export",
        "search_conversations",
        "read_conversation",
        "debate",
        "folders",
    }
)

ANTHROPIC_TOOL_SEARCH: dict[str, str] = {
    "type": "tool_search_tool_bm25_20251119",
    "name": "tool_search_tool_bm25",
}
ANTHROPIC_TOOL_SEARCH_BETA = "advanced-tool-use-2025-11-20"

OPENAI_TOOL_SEARCH: dict[str, str] = {"type": "tool_search"}

WireProtocol = Literal["chat_completions", "anthropic_messages", "openai_responses"]

# Relays that have not confirmed they forward tool-search / defer_loading.
# Until they do, treat as no vendor mechanism (full visible table).
_CONFIRMED_TOOL_SEARCH_VENDORS: frozenset[str] = frozenset({"openai", "anthropic"})


def is_deferred_opening_tool(name: str) -> bool:
    key = (name or "").strip()
    return key in DEFERRED_OPENING_TOOL_NAMES or is_mcp_tool_name(key)


def should_stamp_vendor_defer_loading(
    *,
    model: str,
    vendor_id: str,
    protocol: WireProtocol,
) -> bool:
    """True only when model + official vendor + native protocol all line up.

    Known before send. No 400-retry probe.
    """
    if protocol == "chat_completions":
        return False
    vid = (vendor_id or "").strip()
    if vid not in _CONFIRMED_TOOL_SEARCH_VENDORS:
        return False
    if protocol == "openai_responses" and vid != "openai":
        return False
    if protocol == "anthropic_messages" and vid != "anthropic":
        return False
    from agentcore.llm.provider.wire_dialect import resolve_wire_dialect

    return resolve_wire_dialect(model).defer_loading


def openai_tool_name(item: dict) -> str:
    fn = item.get("function")
    if isinstance(fn, dict):
        return str(fn.get("name") or "")
    return str(item.get("name") or "")


def stamp_anthropic_defer_loading(tools: list[dict]) -> list[dict]:
    """Annotate an already-converted Anthropic ``tools`` list. Never mutates input."""
    if not tools:
        return tools
    out: list[dict] = []
    any_deferred = False
    for item in tools:
        cloned = dict(item)
        name = str(cloned.get("name") or "")
        if is_deferred_opening_tool(name):
            cloned["defer_loading"] = True
            any_deferred = True
        out.append(cloned)
    if not any_deferred:
        return out
    if any(str(t.get("type") or "").startswith("tool_search_tool") for t in out):
        return out
    return [{**ANTHROPIC_TOOL_SEARCH}, *out]


def stamp_openai_responses_defer_loading(tools: list[dict]) -> list[dict]:
    """Annotate an already-converted Responses ``tools`` list. Never mutates input."""
    if not tools:
        return tools
    out: list[dict] = []
    any_deferred = False
    for item in tools:
        cloned = dict(item)
        name = str(cloned.get("name") or "")
        if is_deferred_opening_tool(name):
            cloned["defer_loading"] = True
            any_deferred = True
        out.append(cloned)
    if not any_deferred:
        return out
    if any(t.get("type") == "tool_search" for t in out):
        return out
    return [{**OPENAI_TOOL_SEARCH}, *out]


def anthropic_tool_search_headers(payload: dict) -> dict[str, str]:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return {}
    if any(
        isinstance(t, dict) and str(t.get("type") or "").startswith("tool_search_tool")
        for t in tools
    ):
        return {"anthropic-beta": ANTHROPIC_TOOL_SEARCH_BETA}
    return {}
