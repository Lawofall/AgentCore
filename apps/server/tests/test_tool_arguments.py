"""Wire ``function.arguments`` must be valid JSON (OpenAI-compatible providers)."""

import json

from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest, ToolCall, ToolCallFunction
from agentcore.llm.tool_arguments import coerce_openai_tool_arguments


def test_coerce_empty_and_invalid_to_empty_object():
    assert coerce_openai_tool_arguments("") == "{}"
    assert coerce_openai_tool_arguments("   ") == "{}"
    assert coerce_openai_tool_arguments(None) == "{}"
    assert coerce_openai_tool_arguments("{@@@}") == "{}"
    assert json.loads(coerce_openai_tool_arguments("")) == {}


def test_coerce_passes_through_valid_json():
    assert coerce_openai_tool_arguments('{"pattern":"nav"}') == '{"pattern":"nav"}'
    assert coerce_openai_tool_arguments("[]") == "[]"
    assert coerce_openai_tool_arguments("null") == "null"


def test_build_payload_coerces_empty_and_invalid_tool_arguments():
    """Next-turn history with empty / illegal args must not leave the slot blank.

    OpenCode Go 400s ``Assistant tool call function.arguments must be valid JSON``
    and we treat that as non-retryable — the worker dies after files already landed.
    """
    provider = OpenAICompatibleProvider(name="test", api_key="k", base_url="http://x/v1")
    req = LLMRequest(
        messages=[
            LLMMessage(role="user", content="go"),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        id="empty",
                        function=ToolCallFunction(name="grep", arguments=""),
                    ),
                    ToolCall(
                        id="bad",
                        function=ToolCallFunction(name="grep", arguments="{@@@}"),
                    ),
                    ToolCall(
                        id="ok",
                        function=ToolCallFunction(
                            name="grep", arguments='{"pattern":"nav"}'
                        ),
                    ),
                ],
            ),
        ],
        model=DEEPSEEK_V4_FLASH,
    )
    payload = provider._build_payload(req, stream=True)
    args = [
        tc["function"]["arguments"]
        for tc in payload["messages"][1]["tool_calls"]
    ]
    assert args[0] == "{}"
    assert args[1] == "{}"
    assert args[2] == '{"pattern":"nav"}'
    for raw in args:
        json.loads(raw)
