"""Platform leaf — resolve upstream credentials per ``request.model``.

运营中转「一 key 一模型」(``PLATFORM_MODEL_CREDENTIALS``) 要求同一 ``platform/``
路由前缀下，不同 model id 使用不同 api_key。冻结单 key 的
``OpenAICompatibleProvider`` 无法表达这一点；本 leaf 在每次调用时经
``platform_llm_credentials(model=…)`` 取对 key，并按 (api_key, base_url) 缓存
底层 HTTP 客户端。

可选 ``upstream_model``：目录 id 可与上游 id 不同（如 ``glm-5.2-alt`` →
上游仍发 ``glm-5.2``）。凭据 lookup 用目录 id；出站前改写
``request.model``，调用方持有的 request 不变（计费仍按目录 id）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

from agentcore.core.errors import LLMError
from agentcore.llm.provider.openai_compatible import OpenAICompatibleProvider
from agentcore.llm.provider.protocol import LLMChunk, LLMRequest, LLMResponse

_LeafKey = tuple[str, str]  # (api_key, base_url)


class PlatformProvider:
    """Router leaf for ``PLATFORM_PROVIDER_SENTINEL`` — credentials follow the model."""

    def __init__(self) -> None:
        self._leaves: dict[_LeafKey, OpenAICompatibleProvider] = {}

    @property
    def name(self) -> str:
        return "platform"

    @property
    def display_name(self) -> str:
        return "平台"

    @property
    def base_url(self) -> str | None:
        # Per-model upstream; no single leaf URL until a request picks a model.
        from agentcore.config import settings

        return (settings.platform_base_url or "").rstrip("/") or None

    def clone(self) -> PlatformProvider:
        """Independent leaf cache (coordination drive ownership)."""
        return PlatformProvider()

    async def close(self) -> None:
        for leaf in self._leaves.values():
            await leaf.close()
        self._leaves.clear()

    def _leaf_for(self, model: str) -> OpenAICompatibleProvider:
        # Lazy import: provider package init must not pull resolve → credentials → profiles.
        from agentcore.llm.resolve import platform_llm_credentials

        mid = (model or "").strip()
        creds = platform_llm_credentials(model=mid or None)
        if creds is None:
            from agentcore.llm.platform_pool_scheduler import (
                platform_pool_unavailable_error,
                pool_has_enabled_members,
            )

            if pool_has_enabled_members():
                raise platform_pool_unavailable_error(blocked=True)
            label = mid or "(empty)"
            raise LLMError(
                f"平台模型 {label} 无可用凭据，"
                "请检查 PLATFORM_API_KEY / PLATFORM_MODEL_CREDENTIALS"
            )
        key: _LeafKey = (creds.api_key, creds.base_url)
        leaf = self._leaves.get(key)
        if (
            leaf is None
            or leaf._api_key != creds.api_key
            or leaf._base_url.rstrip("/") != creds.base_url.rstrip("/")
        ):
            leaf = OpenAICompatibleProvider(
                name="platform",
                api_key=creds.api_key,
                base_url=creds.base_url,
                extra_headers=creds.extra_headers,
                display_name="平台",
            )
            self._leaves[key] = leaf
        return leaf

    def _wire_request(self, request: LLMRequest) -> LLMRequest:
        from agentcore.llm.resolve import platform_wire_model

        wire = platform_wire_model(request.model)
        if wire == request.model:
            return request
        return replace(request, model=wire)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        leaf = self._leaf_for(request.model)
        return await leaf.complete(self._wire_request(request))

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        leaf = self._leaf_for(request.model)
        return leaf.stream(self._wire_request(request))
