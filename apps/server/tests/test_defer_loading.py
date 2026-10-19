"""Vendor defer_loading: frozen opening table, stamp only on confirmed wires."""

from __future__ import annotations

from agentcore.llm.provider.anthropic_messages import (
    build_messages_payload,
)
from agentcore.llm.provider.anthropic_messages import (
    convert_tools as convert_anthropic_tools,
)
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.openai_responses import (
    build_responses_payload,
)
from agentcore.llm.provider.openai_responses import (
    convert_tools as convert_responses_tools,
)
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest
from agentcore.llm.provider.wire_dialect import resolve_wire_dialect
from agentcore.tools.defer_loading import (
    DEFERRED_OPENING_TOOL_NAMES,
    is_deferred_opening_tool,
    should_stamp_vendor_defer_loading,
)
from agentcore.tools.registration import declared_tool_name, declared_tools

_OPENING = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "read a file",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "debate",
            "description": "start a debate",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_conversations",
            "description": "search chats",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_github_list_issues",
            "description": "list issues",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _request(model: str) -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
        tools=_OPENING,
    )


def test_deferred_roster_is_declared_and_excludes_core_loop():
    declared = {declared_tool_name(cls) for cls in declared_tools()}
    assert declared >= DEFERRED_OPENING_TOOL_NAMES
    for name in (
        "read",
        "write",
        "edit",
        "grep",
        "glob",
        "file_list",
        "run",
        "ask_user",
        "delegate",
        "consult",
        "web_search",
        "web_fetch",
        "host",
        "browser",
        "file_delete",
        "file_batch",
    ):
        assert not is_deferred_opening_tool(name), name
    assert is_deferred_opening_tool("search_conversations")
    assert is_deferred_opening_tool("read_conversation")
    assert is_deferred_opening_tool("debate")
    assert is_deferred_opening_tool("folders")
    assert is_deferred_opening_tool("mcp_slack_post_message")


def test_dialect_marks_supported_families_not_deepseek():
    assert resolve_wire_dialect("deepseek-v4.1-flash").defer_loading is False
    assert resolve_wire_dialect("gpt-4o").defer_loading is False
    assert resolve_wire_dialect("gpt-5").defer_loading is False
    assert resolve_wire_dialect("claude-opus-4").defer_loading is False
    assert resolve_wire_dialect("claude-haiku-4-5").defer_loading is True
    assert resolve_wire_dialect("claude-sonnet-4-5").defer_loading is True
    assert resolve_wire_dialect("gpt-5.4").defer_loading is True
    assert resolve_wire_dialect("gpt-6-astra").defer_loading is True


def test_stamp_gate_rejects_completions_and_opencode():
    assert (
        should_stamp_vendor_defer_loading(
            model="gpt-5.4",
            vendor_id="openai",
            protocol="chat_completions",
        )
        is False
    )
    assert (
        should_stamp_vendor_defer_loading(
            model="claude-haiku-4-5",
            vendor_id="opencode_zen",
            protocol="anthropic_messages",
        )
        is False
    )
    assert (
        should_stamp_vendor_defer_loading(
            model="gpt-5.4",
            vendor_id="opencode_go",
            protocol="openai_responses",
        )
        is False
    )
    assert (
        should_stamp_vendor_defer_loading(
            model="gpt-5.4",
            vendor_id="openai",
            protocol="openai_responses",
        )
        is True
    )
    assert (
        should_stamp_vendor_defer_loading(
            model="claude-haiku-4-5",
            vendor_id="anthropic",
            protocol="anthropic_messages",
        )
        is True
    )


def test_anthropic_convert_stamps_only_when_asked():
    plain = convert_anthropic_tools(_OPENING)
    assert [t["name"] for t in plain] == [
        "read",
        "debate",
        "search_conversations",
        "mcp_github_list_issues",
    ]
    assert all("defer_loading" not in t for t in plain)

    stamped = convert_anthropic_tools(_OPENING, defer_loading=True)
    assert stamped[0]["type"] == "tool_search_tool_bm25_20251119"
    by_name = {t.get("name"): t for t in stamped}
    assert "defer_loading" not in by_name["read"]
    assert by_name["debate"]["defer_loading"] is True
    assert by_name["search_conversations"]["defer_loading"] is True
    assert by_name["mcp_github_list_issues"]["defer_loading"] is True


def test_responses_convert_stamps_only_when_asked():
    plain = convert_responses_tools(_OPENING)
    assert all(t.get("type") == "function" for t in plain)
    assert all("defer_loading" not in t for t in plain)

    stamped = convert_responses_tools(_OPENING, defer_loading=True)
    assert stamped[0] == {"type": "tool_search"}
    by_name = {t.get("name"): t for t in stamped if t.get("type") == "function"}
    assert "defer_loading" not in by_name["read"]
    assert by_name["debate"]["defer_loading"] is True


def test_opencode_messages_payload_does_not_stamp():
    payload = build_messages_payload(
        _request("claude-haiku-4-5"),
        stream=False,
        vendor_id="opencode_zen",
    )
    names = [t.get("name") for t in payload["tools"]]
    assert names[0] != "tool_search_tool_bm25"
    assert all("defer_loading" not in t for t in payload["tools"])


def test_official_openai_responses_gpt4o_does_not_stamp():
    payload = build_responses_payload(
        _request("gpt-4o"),
        stream=False,
        vendor_id="openai",
    )
    assert payload["tools"][0]["type"] == "function"
    assert all("defer_loading" not in t for t in payload["tools"])


def test_official_anthropic_messages_payload_stamps_haiku_4_5():
    payload = build_messages_payload(
        _request("claude-haiku-4-5"),
        stream=False,
        vendor_id="anthropic",
    )
    assert payload["tools"][0]["type"] == "tool_search_tool_bm25_20251119"


def test_official_openai_responses_payload_stamps_gpt_5_4():
    payload = build_responses_payload(
        _request("gpt-5.4"),
        stream=False,
        vendor_id="openai",
    )
    assert payload["tools"][0] == {"type": "tool_search"}
    debate = next(t for t in payload["tools"] if t.get("name") == "debate")
    assert debate["defer_loading"] is True
    read = next(t for t in payload["tools"] if t.get("name") == "read")
    assert "defer_loading" not in read


def test_completions_deepseek_keeps_opening_table_bytes():
    provider = OpenAICompatibleProvider(
        name="user",
        api_key="sk-test",
        base_url="https://api.deepseek.com",
    )
    payload = provider._build_payload(_request("deepseek-v4.1-flash"), stream=False)
    assert payload["tools"] is _OPENING
    assert all("defer_loading" not in t.get("function", t) for t in payload["tools"])
