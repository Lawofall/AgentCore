"""QwenVLReader — a :class:`~agentcore.vision.protocol.VisionReader` over Qwen-VL.

Reads a board PNG (手绘 / 截图) via Alibaba DashScope's **OpenAI-compatible**
``/chat/completions`` endpoint (AI协作白板.md §九.4). The image rides as a standard
multimodal user message: a ``text`` part (the brief prompt) + an ``image_url`` part whose
URL is a ``data:image/png;base64,…`` data URL. Mirrors
:class:`~agentcore.llm.openai_compatible.OpenAICompatibleProvider`'s HTTP shape (Bearer
auth, ``base_url`` with version prefix, typed status mapping + bounded retry), but stays a
self-contained one-shot reader — no streaming, no tool calls, no ``LLMRequest`` (which has
no image content). A fresh ``httpx.AsyncClient`` is opened per call (vision reads are rare),
so there is no client lifecycle for the pipeline to manage.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from agentcore.core.errors import (
    LLMAuthError,
    LLMError,
    LLMInsufficientBalanceError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from agentcore.core.logging import get_logger
from agentcore.core.net import outbound_async_client
from agentcore.core.task_cancel import raise_if_task_cancelled
from agentcore.llm.observability import log_llm_call
from agentcore.llm.provider.protocol import TokenUsage
from agentcore.vision.protocol import VisionReading

logger = get_logger(__name__)

_MAX_RETRIES = 3
_INITIAL_BACKOFF = 1.0
_BACKOFF_MULTIPLIER = 2.0


def _usage_from(usage_data: dict) -> TokenUsage:
    """Parse the ``usage`` block, incl. DashScope's ``prompt_tokens_details.cached_tokens``
    prefix-cache split (protocol.py) — so Qwen cache hits price at the discounted rate
    instead of always billing the whole prompt as a miss (llm/pricing.py QWEN_VL_MAX)."""
    return TokenUsage.from_openai_wire(usage_data)


def _content_text(content: object) -> str:
    """Coerce a chat ``message.content`` (str, or a list of typed parts) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


class QwenVLReader:
    """Vision reader backed by a Qwen-VL OpenAI-compatible ``/chat/completions``."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        name: str = "qwen-vl",
        transport: httpx.AsyncBaseTransport | None = None,
        credential_source: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._name = name
        # Injected only in tests (httpx.MockTransport); None ⇒ real network.
        self._transport = transport
        # Pricing origin for vision_run_cost: BYOK slot → "user", platform → "platform".
        self.credential_source = credential_source

    async def read(self, png_base64: str, prompt: str) -> VisionReading:
        """Return Qwen-VL's reading of ``png_base64`` guided by ``prompt``.

        The :class:`VisionReading` carries the text reading + the call's token usage / model,
        so ``BoardReadTool`` can bill the sub-call into the turn's cost ledger (§九.4 Gap ②).
        Raises a typed :mod:`agentcore.core.errors` LLM error on auth / balance / rate /
        timeout / server failure or an empty reply — ``BoardReadTool`` catches it and maps
        it to a clean tool error, so a bad key or down provider never hangs the turn.
        """
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{png_base64}"
                            },
                        },
                    ],
                }
            ],
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        start = time.monotonic()
        async with outbound_async_client(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(self._timeout, connect=10.0),
            transport=self._transport,
        ) as client:
            data = await self._post_with_retry(client, payload)
        latency_ms = int((time.monotonic() - start) * 1000)

        choice = (data.get("choices") or [{}])[0]
        text = _content_text(choice.get("message", {}).get("content"))
        usage = _usage_from(data.get("usage", {}))
        # Observability log only (the dev.jsonl LLM-call trace) — the cost ledger is billed
        # separately by BoardReadTool off the returned VisionReading.usage (§九.4 Gap ②).
        # ``content`` is the reading (safe to log); the request messages carry the base64
        # image, so they are deliberately NOT passed.
        log_llm_call(
            scenario="vision.board_read",
            model=data.get("model", self._model),
            usage=usage,
            finish_reason=choice.get("finish_reason", "stop"),
            latency_ms=latency_ms,
            stream=False,
            content=text,
        )
        if not text.strip():
            raise LLMError(f"{self._name} 返回空内容（未能解读图像）")
        return VisionReading(text=text, usage=usage, model=data.get("model", self._model))

    def _raise_for_status(self, status_code: int, backoff: float, headers) -> None:
        if status_code == 429:
            retry_after = float(headers.get("retry-after", backoff))
            raise LLMRateLimitError(retry_after=retry_after)
        if status_code in (401, 403):
            raise LLMAuthError(provider_name=self._name)
        if status_code == 402:
            raise LLMInsufficientBalanceError()
        if status_code >= 500:
            raise LLMError(f"{self._name} 服务端错误（{status_code}），请稍后再试")

    async def _post_with_retry(self, client: httpx.AsyncClient, payload: dict) -> dict:
        last_error: Exception | None = None
        backoff = _INITIAL_BACKOFF
        for attempt in range(_MAX_RETRIES):
            raise_if_task_cancelled()
            try:
                response = await client.post("/chat/completions", json=payload)
                self._raise_for_status(response.status_code, backoff, response.headers)
                response.raise_for_status()
                return response.json()
            except (LLMRateLimitError, LLMError) as e:
                last_error = e
                if not e.retryable or attempt == _MAX_RETRIES - 1:
                    raise
                retry_after = e.retry_after if isinstance(e, LLMRateLimitError) else None
                wait = retry_after or backoff
                logger.warning(
                    "vision.retry", provider=self._name, attempt=attempt + 1, wait=wait
                )
                await asyncio.sleep(wait)
                backoff *= _BACKOFF_MULTIPLIER
            except httpx.TimeoutException as e:
                raise_if_task_cancelled(e)
                last_error = LLMTimeoutError(f"连接 {self._name} 超时，请检查网络后重试")
                if attempt == _MAX_RETRIES - 1:
                    raise last_error from e
                logger.warning("vision.timeout_retry", provider=self._name, attempt=attempt + 1)
                await asyncio.sleep(backoff)
                backoff *= _BACKOFF_MULTIPLIER
        raise last_error or LLMError(f"{self._name} 多次重试后仍失败，请稍后重试")
