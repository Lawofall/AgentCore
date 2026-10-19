"""Per-request protocol dispatch: one key, three HTTP APIs.

``/chat/completions`` stays the default. Dispatch reads saved vendor identity,
not whether ``base_url`` equals a preset host. OpenAI vendor →
:class:`OpenAIResponsesProvider` for every chat turn. OpenCode ``/messages``
ids → :class:`AnthropicMessagesProvider`; OpenCode ``gpt-`` / ``grok-`` /
``muse-*`` → Responses. Platform allowlist rows that still need a protocol we
do not speak stay grey. Vendor echo (Anthropic thinking, OpenAI reasoning
items) round-trips on ``LLMMessage.thinking_blocks``. Sidecar keeps speaking
Completions to the inference proxy; this leaf runs after the proxy rebuilds
``LLMRequest``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from agentcore.llm.byok_provider_presets import (
    uses_anthropic_messages_wire,
    uses_openai_responses_wire,
)
from agentcore.llm.provider.anthropic import AnthropicMessagesProvider
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMChunk, LLMRequest, LLMResponse
from agentcore.llm.provider.responses import OpenAIResponsesProvider


class ProtocolDispatchProvider:
    """Leaf that picks Completions / ``/messages`` / ``/responses`` from ``request.model``."""

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        extra_headers: dict[str, str] | None = None,
        display_name: str | None = None,
        vendor_id: str = "custom",
    ) -> None:
        self._name = name
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._extra_headers = dict(extra_headers) if extra_headers else None
        self._display_name = display_name
        self._vendor_id = (vendor_id or "").strip() or "custom"
        self._openai = OpenAICompatibleProvider(
            name=name,
            api_key=api_key,
            base_url=base_url,
            extra_headers=extra_headers,
            display_name=display_name,
        )
        self._anthropic: AnthropicMessagesProvider | None = None
        self._responses: OpenAIResponsesProvider | None = None

    def _leaf_kwargs(self) -> dict:
        return {
            "name": self._name,
            "api_key": self._api_key,
            "base_url": self._base_url,
            "extra_headers": self._extra_headers,
            "display_name": self._display_name,
        }

    def _off_protocol_kwargs(self) -> dict:
        return {**self._leaf_kwargs(), "vendor_id": self._vendor_id}

    def _leaf(self, model: str):
        if uses_anthropic_messages_wire(model, self._vendor_id):
            if self._anthropic is None:
                self._anthropic = AnthropicMessagesProvider(**self._off_protocol_kwargs())
            return self._anthropic
        if uses_openai_responses_wire(model, self._vendor_id):
            if self._responses is None:
                self._responses = OpenAIResponsesProvider(**self._off_protocol_kwargs())
            return self._responses
        return self._openai

    @property
    def name(self) -> str:
        return self._openai.name

    @property
    def display_name(self) -> str:
        return self._openai.display_name

    @property
    def base_url(self) -> str:
        return self._openai.base_url

    def clone(self) -> ProtocolDispatchProvider:
        return ProtocolDispatchProvider(
            **self._leaf_kwargs(),
            vendor_id=self._vendor_id,
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        return await self._leaf(request.model).complete(request)

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        return self._leaf(request.model).stream(request)

    async def probe(self, *, model: str) -> None:
        await self._leaf(model).probe(model=model)

    async def probe_tools(self, *, model: str) -> bool | None:
        probe_tools = getattr(self._leaf(model), "probe_tools", None)
        if probe_tools is None:
            return None
        return await probe_tools(model=model)

    async def list_models(self) -> list[str]:
        return await self._openai.list_models()

    async def close(self) -> None:
        await self._openai.close()
        if self._anthropic is not None:
            await self._anthropic.close()
        if self._responses is not None:
            await self._responses.close()


def build_credential_leaf(
    *,
    name: str,
    api_key: str,
    base_url: str,
    extra_headers: dict[str, str] | None = None,
    display_name: str | None = None,
    vendor_id: str = "custom",
) -> ProtocolDispatchProvider:
    """BYOK / platform-member leaf: dispatch chat vs /messages vs /responses."""
    return ProtocolDispatchProvider(
        name=name,
        api_key=api_key,
        base_url=base_url,
        extra_headers=extra_headers,
        display_name=display_name,
        vendor_id=vendor_id,
    )
