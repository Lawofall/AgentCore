"""LLMRequest ↔ OpenAI /responses mapping (no live upstream)."""

from __future__ import annotations

import json

import httpx
import pytest

pytestmark = pytest.mark.anyio

from agentcore.llm.provider.openai_responses import (
    RESPONSES_PATH,
    ResponsesSseAssembler,
    build_responses_payload,
    convert_tools,
    fold_input,
    map_status,
    parse_unary_response,
    reasoning_payload,
)
from agentcore.llm.provider.protocol import (
    LLMMessage,
    LLMRequest,
    TokenUsage,
    ToolCall,
    ToolCallFunction,
    normalize_thinking_blocks,
)
from agentcore.llm.provider.responses import OpenAIResponsesProvider

_OPENAI = "https://api.openai.com/v1"


def _tool(name: str, arguments: str, call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, function=ToolCallFunction(name=name, arguments=arguments))


def test_convert_tools_flattens_openai_function_shape_and_disables_strict():
    tools = convert_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "read",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            }
        ]
    )
    assert tools == [
        {
            "type": "function",
            "name": "read_file",
            "description": "read",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
            "strict": False,
        }
    ]


def test_fold_input_peels_system_and_emits_items():
    instructions, items = fold_input(
        [
            LLMMessage(role="system", content="be brief"),
            LLMMessage(role="user", content="hi"),
            LLMMessage(
                role="assistant",
                content="",
                reasoning_content="plan",
                thinking_blocks=[
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "encrypted_content": "enc",
                    }
                ],
                tool_calls=[_tool("read_file", '{"path":"a"}')],
            ),
            LLMMessage(role="tool", content="ok", tool_call_id="call_1"),
        ]
    )
    assert instructions == "be brief"
    assert items[0] == {"type": "message", "role": "user", "content": "hi"}
    assert items[1]["type"] == "reasoning"
    assert items[1]["id"] == "rs_1"
    assert items[1]["encrypted_content"] == "enc"
    assert items[2]["type"] == "function_call"
    assert items[2]["call_id"] == "call_1"
    assert items[3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "ok",
    }


def test_fold_input_maps_image_url_to_input_image():
    _, items = fold_input(
        [
            LLMMessage(
                role="user",
                content=[
                    {"type": "text", "text": "看图"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,aa"},
                    },
                ],
            )
        ]
    )
    assert items[0]["type"] == "message"
    assert items[0]["content"][1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,aa",
    }


def test_anthropic_thinking_is_not_echoed_on_responses_wire():
    _, items = fold_input(
        [
            LLMMessage(
                role="assistant",
                content="hi",
                thinking_blocks=[
                    {"type": "thinking", "thinking": "plan", "signature": "sig"}
                ],
            )
        ]
    )
    assert items == [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hi"}],
        }
    ]


def test_build_payload_store_false_and_gpt6_omits_temperature():
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="gpt-6-astra",
        temperature=0.7,
        max_tokens=1024,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "x",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        reasoning_effort="high",
    )
    payload = build_responses_payload(req, stream=True, base_url=_OPENAI)
    assert payload["store"] is False
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["stream"] is True
    assert payload["max_output_tokens"] == 1024
    assert "temperature" not in payload
    assert payload["reasoning"] == {"effort": "high", "summary": "auto"}
    assert payload["tools"][0]["strict"] is False
    assert payload["input"] == [
        {"type": "message", "role": "user", "content": "hi"}
    ]


def test_build_payload_gpt4o_keeps_temperature_omits_reasoning():
    req = LLMRequest(
        messages=[
            LLMMessage(role="system", content="sys"),
            LLMMessage(role="user", content="hi"),
        ],
        model="gpt-4o",
        temperature=0.4,
    )
    payload = build_responses_payload(req, stream=False, base_url=_OPENAI)
    assert payload["temperature"] == 0.4
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert "reasoning" not in payload
    assert payload["instructions"] == "sys"


def test_reasoning_payload_background_uses_lowest_token():
    req = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="gpt-5",
        thinking=False,
    )
    assert reasoning_payload(req) == {"effort": "minimal"}
    astra = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="gpt-6-astra",
        thinking=False,
    )
    assert reasoning_payload(astra) == {"effort": "low"}
    chat = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="gpt-5",
        reasoning_effort="medium",
    )
    assert reasoning_payload(chat) == {"effort": "medium", "summary": "auto"}


def test_parse_unary_response_tools_and_reasoning():
    parsed = parse_unary_response(
        {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "encrypted_content": "enc",
                    "summary": [{"type": "summary_text", "text": "plan"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "read_file",
                    "arguments": '{"path":"a"}',
                },
            ],
        }
    )
    assert parsed.finish_reason == "tool_calls"
    assert parsed.reasoning == "plan"
    assert parsed.tool_calls is not None
    assert parsed.tool_calls[0].id == "call_1"
    assert parsed.thinking_blocks == [
        {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc",
         "summary": [{"type": "summary_text", "text": "plan"}]}
    ]


def test_map_status_incomplete_is_length():
    assert map_status("incomplete", has_tool_calls=False, incomplete_reason="max_output_tokens") == "length"
    assert map_status("completed", has_tool_calls=False, incomplete_reason=None) == "stop"


def test_from_responses_wire_usage():
    usage = TokenUsage.from_responses_wire(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 40},
            "output_tokens_details": {"reasoning_tokens": 8},
        }
    )
    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    assert usage.cache_hit_tokens == 40
    assert usage.cache_miss_tokens == 60
    assert usage.reasoning_tokens == 8


def test_sse_assembler_text_and_function_call():
    assembler = ResponsesSseAssembler()
    chunks: list = []
    for line in (
        'event: response.output_item.added',
        'data: {"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","call_id":"call_1","name":"read_file"}}',
        "",
        'event: response.function_call_arguments.delta',
        'data: {"type":"response.function_call_arguments.delta","output_index":0,"delta":"{\\"path\\":\\"a\\"}"}',
        "",
        'event: response.completed',
        'data: {"type":"response.completed","response":{"status":"completed","output":[{"type":"function_call","call_id":"call_1","name":"read_file","arguments":"{\\"path\\":\\"a\\"}"}],"usage":{"input_tokens":3,"output_tokens":2}}}',
        "",
    ):
        chunks.extend(assembler.feed_line(line))
    assert chunks[0].delta_tool_calls is not None
    assert chunks[0].delta_tool_calls[0].id == "call_1"
    assert chunks[1].delta_tool_calls is not None
    assert chunks[-1].finish_reason == "tool_calls"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.input_tokens == 3


def test_sse_assembler_echoes_reasoning_item():
    assembler = ResponsesSseAssembler()
    chunks: list = []
    for line in (
        "event: response.output_item.done",
        'data: {"type":"response.output_item.done","output_index":0,"item":{"type":"reasoning","id":"rs_1","encrypted_content":"enc","summary":[{"type":"summary_text","text":"plan"}]}}',
        "",
        "event: response.reasoning_summary_text.delta",
        'data: {"type":"response.reasoning_summary_text.delta","delta":"plan"}',
        "",
        "event: response.completed",
        'data: {"type":"response.completed","response":{"status":"completed","output":[{"type":"reasoning","id":"rs_1","encrypted_content":"enc","summary":[{"type":"summary_text","text":"plan"}]},{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}]}}',
        "",
    ):
        chunks.extend(assembler.feed_line(line))
    assert any(c.delta_reasoning == "plan" for c in chunks)
    finish = chunks[-1]
    assert finish.finish_reason == "stop"
    assert finish.thinking_blocks == [
        {
            "type": "reasoning",
            "id": "rs_1",
            "encrypted_content": "enc",
            "summary": [{"type": "summary_text", "text": "plan"}],
        }
    ]


def test_normalize_keeps_reasoning_drops_unsigned_thinking():
    kept = normalize_thinking_blocks(
        [
            {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc"},
            {"type": "thinking", "thinking": "no-sig"},
        ]
    )
    assert kept == [{"type": "reasoning", "id": "rs_1", "encrypted_content": "enc"}]


def _request(model: str = "gpt-4o") -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model=model,
        stream=False,
    )


async def test_responses_complete_posts_responses_path():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "model": "gpt-4o",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "pong"}],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    provider = OpenAIResponsesProvider(
        name="user", api_key="sk-test", base_url=_OPENAI
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        base_url=_OPENAI, transport=httpx.MockTransport(handler)
    )
    try:
        response = await provider.complete(_request("gpt-4o"))
        assert response.content == "pong"
        assert captured["path"] == "/v1/responses"
        assert captured["path"].endswith(RESPONSES_PATH)
        assert captured["body"]["store"] is False
        assert captured["body"]["include"] == ["reasoning.encrypted_content"]
        assert captured["body"]["model"] == "gpt-4o"
        assert captured["body"]["stream"] is False
    finally:
        await provider.close()
