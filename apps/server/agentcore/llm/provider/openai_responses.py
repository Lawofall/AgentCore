"""OpenAI-shaped ``LLMRequest`` ↔ Responses API ``POST /responses`` wire.

Engine / sidecar keep ``LLMMessage`` / ``LLMChunk``. This module is the only
place that knows Items (``message`` / ``reasoning`` / ``function_call`` /
``function_call_output``), ``instructions``, ``store: false``, and Responses
SSE event names.

State: we own the transcript. Never ``previous_response_id``. ``store`` is
always false so user turns are not retained on the vendor. Stateless /
ZDR tool rounds request ``include: reasoning.encrypted_content`` and echo
those items on the next turn. Chat/CEO/worker also set ``reasoning.summary``
to ``auto`` so the thought panel has text; background one-shots omit the
summary. Hosted vendor tools (``web_search`` …) are not sent — product
tools stay ours.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agentcore.llm.provider.protocol import (
    LLMChunk,
    LLMContent,
    LLMMessage,
    LLMRequest,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
    ToolCallFunction,
    llm_content_text,
    responses_echo_items,
)
from agentcore.llm.provider.wire_dialect import resolve_wire_dialect
from agentcore.llm.tool_arguments import coerce_openai_tool_arguments

RESPONSES_PATH = "/responses"
# Official create() includable for store:false / ZDR tool rounds. Newer
# OpenAI defaults this when store is false; sending it keeps OpenCode and
# older snapshots on the same contract. Cookbook still lists the field.
RESPONSES_INCLUDE = ("reasoning.encrypted_content",)


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def convert_tools(tools: list[dict] | None, *, defer_loading: bool = False) -> list[dict]:
    """Chat Completions ``tools[].function`` → Responses internally-tagged functions.

    ``strict: false`` is explicit: our schemas are not Responses-strict, and
    omitting the flag tries strict mode then silently falls back.
    """
    if not tools:
        return []
    out: list[dict] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        fn = item.get("function")
        if item.get("type") == "function" and isinstance(fn, dict):
            params = fn.get("parameters")
            schema = params if isinstance(params, dict) else {"type": "object", "properties": {}}
            out.append(
                {
                    "type": "function",
                    "name": str(fn.get("name") or ""),
                    "description": str(fn.get("description") or ""),
                    "parameters": schema,
                    "strict": False,
                }
            )
            continue
        if item.get("type") == "function" and isinstance(item.get("name"), str):
            cloned = dict(item)
            cloned.setdefault("strict", False)
            out.append(cloned)
    if defer_loading:
        from agentcore.tools.defer_loading import stamp_openai_responses_defer_loading

        return stamp_openai_responses_defer_loading(out)
    return out


def convert_tool_choice(choice: str) -> str:
    if choice in {"none", "required", "auto"}:
        return choice
    return "auto"


def _map_image_part(part: dict) -> dict | None:
    raw = part.get("image_url")
    url = ""
    if isinstance(raw, dict):
        url = str(raw.get("url") or "")
    elif isinstance(raw, str):
        url = raw
    url = url.strip()
    if not url:
        return None
    return {"type": "input_image", "image_url": url}


def _user_content(content: LLMContent) -> str | list[dict]:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[dict] = []
    for part in content:
        if isinstance(part, str):
            if part:
                parts.append({"type": "input_text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text":
            parts.append({"type": "input_text", "text": str(part.get("text") or "")})
        elif kind == "image_url":
            mapped = _map_image_part(part)
            if mapped is not None:
                parts.append(mapped)
    if len(parts) == 1 and parts[0].get("type") == "input_text":
        return str(parts[0].get("text") or "")
    return parts


def _user_item(content: LLMContent) -> dict[str, Any]:
    return {"type": "message", "role": "user", "content": _user_content(content)}


def _assistant_text_item(content: LLMContent) -> dict[str, Any] | None:
    text = llm_content_text(content).strip()
    if not text:
        return None
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def _function_call_item(call: ToolCall) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call.id,
        "name": call.function.name,
        "arguments": coerce_openai_tool_arguments(call.function.arguments),
    }


def _function_call_output_item(message: LLMMessage) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": message.tool_call_id or "",
        "output": llm_content_text(message.content),
    }


def fold_input(messages: list[LLMMessage]) -> tuple[str, list[dict]]:
    """Peel system text into ``instructions``; emit Responses input Items in order."""
    system_parts: list[str] = []
    items: list[dict] = []
    for message in messages:
        if message.role == "system":
            text = llm_content_text(message.content).strip()
            if text:
                system_parts.append(text)
            continue
        if message.role == "user":
            items.append(_user_item(message.content))
            continue
        if message.role == "tool":
            items.append(_function_call_output_item(message))
            continue
        items.extend(responses_echo_items(message.thinking_blocks))
        text_item = _assistant_text_item(message.content)
        if text_item is not None:
            items.append(text_item)
        for call in message.tool_calls or []:
            items.append(_function_call_item(call))
    return "\n\n".join(system_parts), items


def reasoning_payload(request: LLMRequest, *, base_url: str | None = None) -> dict | None:
    """Responses ``reasoning`` object. Omit when the leaf has no effort table.

    Background one-shots (``thinking=False``) send the lowest listed token —
    GPT-6 Astra has no ``none`` — and omit ``summary``. Chat/CEO/worker send
    the stored token or the vendor default, with ``summary: auto``.
    """
    dialect = resolve_wire_dialect(request.model, base_url=base_url)
    options = dialect.reasoning_effort_options
    if not options:
        return None
    if request.thinking is False:
        return {"effort": options[0]}
    stored = (request.reasoning_effort or "").strip()
    effort = stored if stored in options else (dialect.reasoning_effort_default or options[0])
    # Visible CoT is opt-in on Responses; without this the thought panel is empty.
    return {"effort": effort, "summary": "auto"}


def build_responses_payload(
    request: LLMRequest,
    *,
    stream: bool,
    base_url: str | None = None,
    vendor_id: str = "",
) -> dict[str, Any]:
    instructions, items = fold_input(request.messages)
    payload: dict[str, Any] = {
        "model": request.model,
        "input": items,
        "stream": stream,
        "store": False,
        "include": list(RESPONSES_INCLUDE),
    }
    if instructions:
        payload["instructions"] = instructions
    dialect = resolve_wire_dialect(request.model, base_url=base_url)
    if not dialect.omit_temperature:
        payload["temperature"] = request.temperature
    if request.max_tokens:
        payload["max_output_tokens"] = int(request.max_tokens)
    reasoning = reasoning_payload(request, base_url=base_url)
    if reasoning is not None:
        payload["reasoning"] = reasoning
    from agentcore.tools.defer_loading import should_stamp_vendor_defer_loading

    stamp = should_stamp_vendor_defer_loading(
        model=request.model,
        vendor_id=vendor_id,
        protocol="openai_responses",
    )
    tools = convert_tools(request.tools, defer_loading=stamp)
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = convert_tool_choice(request.tool_choice)
    return payload


def map_status(status: str | None, *, has_tool_calls: bool, incomplete_reason: str | None) -> str:
    if incomplete_reason == "max_output_tokens" or status == "incomplete":
        return "length"
    if has_tool_calls:
        return "tool_calls"
    return "stop"


def _summary_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    summary = item.get("summary")
    if isinstance(summary, list):
        for block in summary:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif isinstance(block, str) and block:
                parts.append(block)
    content = item.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return "".join(parts)


def _message_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"output_text", "text"}:
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


@dataclass(frozen=True)
class ParsedUnaryResponse:
    content: str
    reasoning: str | None
    tool_calls: list[ToolCall] | None
    finish_reason: str
    thinking_blocks: list[dict] | None


def parse_unary_response(data: dict[str, Any]) -> ParsedUnaryResponse:
    """Content, reasoning, tools, finish_reason, echoable reasoning items."""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tools: list[ToolCall] = []
    raw_reasoning: list[dict] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "message":
            text_parts.append(_message_text(item))
        elif kind == "reasoning":
            reasoning_parts.append(_summary_text(item))
            raw_reasoning.append(item)
        elif kind == "function_call":
            tools.append(
                ToolCall(
                    id=str(item.get("call_id") or item.get("id") or ""),
                    function=ToolCallFunction(
                        name=str(item.get("name") or ""),
                        arguments=coerce_openai_tool_arguments(
                            item.get("arguments")
                            if isinstance(item.get("arguments"), str)
                            else json.dumps(item.get("arguments") or {})
                        ),
                    ),
                )
            )
    incomplete = data.get("incomplete_details")
    incomplete_reason = None
    if isinstance(incomplete, dict):
        reason = incomplete.get("reason")
        incomplete_reason = reason if isinstance(reason, str) else None
    status = data.get("status")
    finish = map_status(
        status if isinstance(status, str) else None,
        has_tool_calls=bool(tools),
        incomplete_reason=incomplete_reason,
    )
    reasoning = "".join(reasoning_parts) or None
    return ParsedUnaryResponse(
        content="".join(text_parts),
        reasoning=reasoning,
        tool_calls=tools or None,
        finish_reason=finish,
        thinking_blocks=responses_echo_items(raw_reasoning) or None,
    )


def usage_from_responses(data: dict[str, Any] | None) -> TokenUsage:
    raw = data or {}
    return TokenUsage.from_responses_wire(raw)


@dataclass
class ResponsesSseAssembler:
    """Turn Responses SSE events into ``LLMChunk``s."""

    model: str = ""
    stop_reason: str | None = None
    thinking_blocks: list[dict] = field(default_factory=list)
    usage: TokenUsage | None = None
    failed_message: str | None = None
    _event: str = ""
    _tool_index: dict[int, int] = field(default_factory=dict)
    _next_tool: int = 0
    _args_seen: set[int] = field(default_factory=set)
    _open_reasoning: dict[int, dict] = field(default_factory=dict)

    def feed_line(self, line: str) -> list[LLMChunk]:
        stripped = line.strip()
        if not stripped:
            self._event = ""
            return []
        if stripped.startswith("event:"):
            self._event = stripped[6:].strip()
            return []
        if not stripped.startswith("data:"):
            return []
        payload = stripped[5:].strip()
        if not payload or payload == "[DONE]":
            return []
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        kind = self._event or str(data.get("type") or "")
        return self._on_event(kind, data)

    def _on_event(self, kind: str, data: dict[str, Any]) -> list[LLMChunk]:
        if kind in {"response.created", "response.in_progress"}:
            response = _as_dict(data.get("response"))
            model = response.get("model")
            if isinstance(model, str) and model:
                self.model = model
            return []
        if kind == "response.output_item.added":
            return self._on_item_added(data)
        if kind == "response.output_item.done":
            return self._on_item_done(data)
        if kind == "response.output_text.delta":
            text = str(data.get("delta") or "")
            return [LLMChunk(delta_content=text)] if text else []
        if kind in {
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        }:
            text = str(data.get("delta") or "")
            return [LLMChunk(delta_reasoning=text)] if text else []
        if kind == "response.function_call_arguments.delta":
            return self._on_arguments_delta(data)
        if kind == "response.completed":
            return self._on_completed(_as_dict(data.get("response")) or data)
        if kind == "response.failed":
            response = _as_dict(data.get("response"))
            error = _as_dict(response.get("error") or data.get("error"))
            message = error.get("message")
            self.failed_message = (
                str(message) if isinstance(message, str) and message else "上游 Responses 调用失败"
            )
            return []
        if kind == "response.incomplete":
            return self._on_completed(_as_dict(data.get("response")) or data)
        return []

    def _on_item_added(self, data: dict[str, Any]) -> list[LLMChunk]:
        item = _as_dict(data.get("item"))
        index = int(data.get("output_index") or 0)
        kind = item.get("type")
        if kind == "reasoning":
            self._open_reasoning[index] = dict(item)
            return []
        if kind != "function_call":
            return []
        tool_i = self._next_tool
        self._next_tool += 1
        self._tool_index[index] = tool_i
        return [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=tool_i,
                        id=str(item.get("call_id") or item.get("id") or "") or None,
                        function_name=str(item.get("name") or "") or None,
                        arguments_delta="",
                    )
                ]
            )
        ]

    def _on_item_done(self, data: dict[str, Any]) -> list[LLMChunk]:
        item = _as_dict(data.get("item"))
        index = int(data.get("output_index") or 0)
        kind = item.get("type")
        if kind == "reasoning":
            self._open_reasoning.pop(index, None)
            echoed = responses_echo_items([item])
            if echoed:
                self.thinking_blocks.extend(echoed)
            return []
        if kind != "function_call":
            return []
        if index in self._args_seen:
            return []
        arguments = item.get("arguments")
        if not isinstance(arguments, str) or not arguments:
            return []
        tool_i = self._tool_index.get(index)
        if tool_i is None:
            tool_i = self._next_tool
            self._next_tool += 1
            self._tool_index[index] = tool_i
        self._args_seen.add(index)
        return [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(index=tool_i, arguments_delta=arguments)
                ]
            )
        ]

    def _on_arguments_delta(self, data: dict[str, Any]) -> list[LLMChunk]:
        index = int(data.get("output_index") or 0)
        fragment = str(data.get("delta") or "")
        if not fragment:
            return []
        tool_i = self._tool_index.get(index, 0)
        self._args_seen.add(index)
        return [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(index=tool_i, arguments_delta=fragment)
                ]
            )
        ]

    def _on_completed(self, response: dict[str, Any]) -> list[LLMChunk]:
        parsed = parse_unary_response(response)
        usage_raw = response.get("usage")
        self.usage = usage_from_responses(usage_raw if isinstance(usage_raw, dict) else None)
        self.stop_reason = parsed.finish_reason
        if parsed.thinking_blocks:
            self.thinking_blocks = list(parsed.thinking_blocks)
        model = response.get("model")
        if isinstance(model, str) and model:
            self.model = model
        return [self._finish_chunk()]

    def _finish_chunk(self) -> LLMChunk:
        for item in self._open_reasoning.values():
            echoed = responses_echo_items([item])
            if echoed:
                self.thinking_blocks.extend(echoed)
        self._open_reasoning.clear()
        return LLMChunk(
            finish_reason=self.stop_reason or "stop",
            usage=self.usage,
            thinking_blocks=self.thinking_blocks or None,
        )
