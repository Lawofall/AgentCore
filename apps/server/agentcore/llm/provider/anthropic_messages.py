"""OpenAI-shaped ``LLMRequest`` ↔ Anthropic Messages API ``/messages`` wire.

Used by the OpenCode Zen/Go ``/messages`` leaf. Engine / sidecar keep
``LLMMessage`` / ``LLMChunk``; this module is the only place that knows
``system`` / ``tool_use`` / ``tool_result`` / Anthropic SSE event names.
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
    anthropic_echo_blocks,
    llm_content_text,
    normalize_thinking_blocks,
)
from agentcore.llm.provider.wire_dialect import resolve_wire_dialect
from agentcore.llm.tool_arguments import coerce_openai_tool_arguments


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# Anthropic requires max_tokens. Chat profiles often leave it unset.
DEFAULT_MAX_TOKENS = 16_384
ANTHROPIC_VERSION = "2023-06-01"
MIN_THINKING_BUDGET = 1_024
DEFAULT_THINKING_BUDGET = 8_192
_EFFORT_BUDGET = {"low": 2_048, "high": 8_192, "max": 12_288}
_THINKING_MODEL_IDS = frozenset({"union-alpha"})


def model_sends_anthropic_thinking(model_id: str) -> bool:
    """Claude family + Union Alpha on the Anthropic wire; Qwen /messages stays off."""
    mid = (model_id or "").strip().lower()
    return mid.startswith("claude-") or mid in _THINKING_MODEL_IDS


def thinking_payload(request: LLMRequest) -> dict | None:
    """Anthropic extended thinking. Omit = off (unlike DeepSeek, omit ≠ on)."""
    if not model_sends_anthropic_thinking(request.model):
        return None
    if request.thinking is False:
        return {"type": "disabled"}
    if request.thinking is not True:
        return None
    max_tok = int(request.max_tokens or DEFAULT_MAX_TOKENS)
    budget = _EFFORT_BUDGET.get(request.reasoning_effort or "", DEFAULT_THINKING_BUDGET)
    if budget >= max_tok:
        budget = max(MIN_THINKING_BUDGET, max_tok // 2)
    if budget >= max_tok:
        return {"type": "disabled"}
    return {"type": "enabled", "budget_tokens": budget}


def map_stop_reason(reason: str | None) -> str:
    if reason == "tool_use":
        return "tool_calls"
    if reason == "max_tokens":
        return "length"
    return "stop"


def convert_tools(tools: list[dict] | None, *, defer_loading: bool = False) -> list[dict]:
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
                    "name": str(fn.get("name") or ""),
                    "description": str(fn.get("description") or ""),
                    "input_schema": schema,
                }
            )
            continue
        if isinstance(item.get("name"), str) and isinstance(item.get("input_schema"), dict):
            out.append(item)
    if defer_loading:
        from agentcore.tools.defer_loading import stamp_anthropic_defer_loading

        return stamp_anthropic_defer_loading(out)
    return out


def convert_tool_choice(choice: str) -> dict:
    if choice == "none":
        return {"type": "none"}
    if choice == "required":
        return {"type": "any"}
    return {"type": "auto"}


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
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        media = "image/png"
        if "image/" in header:
            media = header.split(";", 1)[0].split(":", 1)[-1].strip() or media
        if not data:
            return None
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media, "data": data},
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


def _content_blocks(content: LLMContent) -> list[dict]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        text = str(content)
        return [{"type": "text", "text": text}] if text else []
    blocks: list[dict] = []
    for part in content:
        if isinstance(part, str):
            if part:
                blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text":
            blocks.append({"type": "text", "text": str(part.get("text") or "")})
        elif kind == "image_url":
            mapped = _map_image_part(part)
            if mapped is not None:
                blocks.append(mapped)
    return blocks


def _assistant_blocks(message: LLMMessage) -> list[dict]:
    echoed = anthropic_echo_blocks(message.thinking_blocks)
    text_blocks = _content_blocks(message.content)
    if (
        len(text_blocks) == 1
        and text_blocks[0].get("type") == "text"
        and not str(text_blocks[0].get("text") or "").strip()
    ):
        text_blocks = []
    blocks: list[dict] = [*echoed, *text_blocks]
    for call in message.tool_calls or []:
        raw_args = coerce_openai_tool_arguments(call.function.arguments)
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.function.name,
                "input": parsed,
            }
        )
    return blocks


def _tool_result_block(message: LLMMessage) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id or "",
        "content": llm_content_text(message.content),
    }


def fold_history(messages: list[LLMMessage]) -> tuple[str, list[dict]]:
    """Peel system text; fold tool results into user; keep user/assistant alternation."""
    system_parts: list[str] = []
    wire: list[dict] = []

    def _append(role: str, content: Any) -> None:
        if content == [] or content is None:
            return
        if role == "assistant" and content == "":
            return
        if wire and wire[-1]["role"] == role:
            prev = wire[-1]["content"]
            if isinstance(prev, str) and isinstance(content, str):
                wire[-1]["content"] = f"{prev}\n\n{content}" if prev else content
                return
            prev_blocks = (
                prev if isinstance(prev, list) else [{"type": "text", "text": prev}]
            )
            next_blocks = (
                content
                if isinstance(content, list)
                else [{"type": "text", "text": content}]
            )
            wire[-1]["content"] = [*prev_blocks, *next_blocks]
            return
        wire.append({"role": role, "content": content})

    pending_tool_results: list[dict] = []

    def _flush_tools() -> None:
        nonlocal pending_tool_results
        if pending_tool_results:
            _append("user", pending_tool_results)
            pending_tool_results = []

    for message in messages:
        if message.role == "system":
            text = llm_content_text(message.content).strip()
            if text:
                system_parts.append(text)
            continue
        if message.role == "tool":
            pending_tool_results.append(_tool_result_block(message))
            continue
        _flush_tools()
        if message.role == "user":
            blocks = _content_blocks(message.content)
            if len(blocks) == 1 and blocks[0].get("type") == "text":
                _append("user", blocks[0]["text"])
            elif blocks:
                _append("user", blocks)
            continue
        blocks = _assistant_blocks(message)
        if blocks:
            _append("assistant", blocks)

    _flush_tools()
    system = "\n\n".join(system_parts)
    return system, wire


def build_messages_payload(
    request: LLMRequest, *, stream: bool, vendor_id: str = ""
) -> dict[str, Any]:
    system, messages = fold_history(request.messages)
    payload: dict[str, Any] = {
        "model": request.model,
        "max_tokens": int(request.max_tokens or DEFAULT_MAX_TOKENS),
        "messages": messages,
        "stream": stream,
    }
    if system:
        payload["system"] = system
    dialect = resolve_wire_dialect(request.model)
    if not dialect.omit_temperature:
        payload["temperature"] = request.temperature
    thinking = thinking_payload(request)
    if thinking is not None:
        payload["thinking"] = thinking
    from agentcore.tools.defer_loading import should_stamp_vendor_defer_loading

    stamp = should_stamp_vendor_defer_loading(
        model=request.model,
        vendor_id=vendor_id,
        protocol="anthropic_messages",
    )
    tools = convert_tools(request.tools, defer_loading=stamp)
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = convert_tool_choice(request.tool_choice)
    return payload


def usage_from_anthropic(data: dict[str, Any] | None) -> TokenUsage:
    raw = data or {}
    input_tokens = int(raw.get("input_tokens") or 0)
    cache_hit = int(raw.get("cache_read_input_tokens") or 0)
    cache_create = int(raw.get("cache_creation_input_tokens") or 0)
    cache_miss = cache_create
    if cache_miss <= 0:
        cache_miss = max(input_tokens - cache_hit, 0)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=int(raw.get("output_tokens") or 0),
        cache_hit_tokens=cache_hit,
        cache_miss_tokens=cache_miss,
        last_prompt_tokens=input_tokens,
    )


@dataclass(frozen=True)
class ParsedUnaryMessage:
    content: str
    reasoning: str | None
    tool_calls: list[ToolCall] | None
    finish_reason: str
    thinking_blocks: list[dict] | None


def parse_unary_message(data: dict[str, Any]) -> ParsedUnaryMessage:
    """Content, reasoning, tools, finish_reason, signed thinking blocks."""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tools: list[ToolCall] = []
    raw_thinking: list[dict] = []
    for block in data.get("content") or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text_parts.append(str(block.get("text") or ""))
        elif kind == "thinking":
            reasoning_parts.append(str(block.get("thinking") or ""))
            raw_thinking.append(block)
        elif kind == "redacted_thinking":
            raw_thinking.append(block)
        elif kind == "tool_use":
            raw_input = block.get("input")
            args = json.dumps(raw_input if isinstance(raw_input, dict) else {}, ensure_ascii=False)
            tools.append(
                ToolCall(
                    id=str(block.get("id") or ""),
                    function=ToolCallFunction(
                        name=str(block.get("name") or ""),
                        arguments=coerce_openai_tool_arguments(args),
                    ),
                )
            )
    stop = data.get("stop_reason")
    finish = map_stop_reason(stop if isinstance(stop, str) else None)
    reasoning = "".join(reasoning_parts) or None
    return ParsedUnaryMessage(
        content="".join(text_parts),
        reasoning=reasoning,
        tool_calls=tools or None,
        finish_reason=finish,
        thinking_blocks=normalize_thinking_blocks(raw_thinking),
    )


@dataclass
class AnthropicSseAssembler:
    """Turn Anthropic SSE events into ``LLMChunk``s."""

    model: str = ""
    input_usage: dict[str, Any] = field(default_factory=dict)
    output_tokens: int = 0
    stop_reason: str | None = None
    thinking_blocks: list[dict] = field(default_factory=list)
    _event: str = ""
    _tool_index: dict[int, int] = field(default_factory=dict)
    _next_tool: int = 0
    _open: dict[int, dict] = field(default_factory=dict)

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
        if kind == "message_start":
            message = _as_dict(data.get("message"))
            self.model = str(message.get("model") or self.model)
            self.input_usage = dict(_as_dict(message.get("usage")))
            return []
        if kind == "content_block_start":
            return self._on_block_start(data)
        if kind == "content_block_delta":
            return self._on_block_delta(data)
        if kind == "content_block_stop":
            self._commit_open(int(data.get("index") or 0))
            return []
        if kind == "message_delta":
            delta = _as_dict(data.get("delta"))
            reason = delta.get("stop_reason")
            if isinstance(reason, str):
                self.stop_reason = reason
            usage = _as_dict(data.get("usage"))
            self.output_tokens = int(usage.get("output_tokens") or self.output_tokens)
            if self.stop_reason:
                return [self._finish_chunk()]
            return []
        if kind == "message_stop" and self.stop_reason is None:
            self.stop_reason = "end_turn"
            return [self._finish_chunk()]
        return []

    def _commit_open(self, index: int) -> None:
        block = self._open.pop(index, None)
        echoed = normalize_thinking_blocks([block] if block else None)
        if echoed:
            self.thinking_blocks.extend(echoed)

    def _on_block_start(self, data: dict[str, Any]) -> list[LLMChunk]:
        block = _as_dict(data.get("content_block"))
        index = int(data.get("index") or 0)
        kind = block.get("type")
        if kind == "thinking":
            self._open[index] = {
                "type": "thinking",
                "thinking": str(block.get("thinking") or ""),
                "signature": str(block.get("signature") or ""),
            }
            return []
        if kind == "redacted_thinking":
            self._open[index] = {
                "type": "redacted_thinking",
                "data": str(block.get("data") or ""),
            }
            return []
        if kind != "tool_use":
            return []
        tool_i = self._next_tool
        self._next_tool += 1
        self._tool_index[index] = tool_i
        return [
            LLMChunk(
                delta_tool_calls=[
                    ToolCallDelta(
                        index=tool_i,
                        id=str(block.get("id") or "") or None,
                        function_name=str(block.get("name") or "") or None,
                        arguments_delta="",
                    )
                ]
            )
        ]

    def _on_block_delta(self, data: dict[str, Any]) -> list[LLMChunk]:
        delta = _as_dict(data.get("delta"))
        kind = delta.get("type")
        index = int(data.get("index") or 0)
        open_block = self._open.get(index)
        if kind == "text_delta":
            text = str(delta.get("text") or "")
            return [LLMChunk(delta_content=text)] if text else []
        if kind == "thinking_delta":
            text = str(delta.get("thinking") or "")
            if open_block is not None and open_block.get("type") == "thinking":
                open_block["thinking"] = str(open_block.get("thinking") or "") + text
            return [LLMChunk(delta_reasoning=text)] if text else []
        if kind == "signature_delta":
            signature = str(delta.get("signature") or "")
            if (
                open_block is not None
                and open_block.get("type") == "thinking"
                and signature
            ):
                open_block["signature"] = signature
            return []
        if kind == "input_json_delta":
            tool_i = self._tool_index.get(index, 0)
            fragment = str(delta.get("partial_json") or "")
            return [
                LLMChunk(
                    delta_tool_calls=[
                        ToolCallDelta(index=tool_i, arguments_delta=fragment)
                    ]
                )
            ]
        return []

    def _finish_chunk(self) -> LLMChunk:
        for index in list(self._open):
            self._commit_open(index)
        usage = usage_from_anthropic(
            {**self.input_usage, "output_tokens": self.output_tokens}
        )
        return LLMChunk(
            finish_reason=map_stop_reason(self.stop_reason),
            usage=usage,
            thinking_blocks=self.thinking_blocks or None,
        )
