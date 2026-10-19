"""Anthropic Messages API leaf — ``POST /messages`` for OpenCode Zen/Go Claude ids."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import httpx

from agentcore.core.errors import (
    LLMClientClosedError,
    LLMError,
    LLMInvalidResponseError,
    LLMTimeoutError,
    LLMUpstreamError,
    is_llm_client_closed_error,
    upstream_rate_limit_error,
)
from agentcore.core.logging import get_logger
from agentcore.core.net import abort_httpx_response
from agentcore.core.task_cancel import raise_if_task_cancelled
from agentcore.llm.credentials import require_http_header_safe_api_key
from agentcore.llm.errors import (
    body_preview,
    client_error_message,
    is_auth_rejection,
    is_balance_exhausted,
    is_non_retryable_client_status,
    opencode_credits_product_message,
    opencode_typed_client_error,
    opencode_typed_rate_limit_message,
    parse_agentcore_error_envelope,
    unsupported_tool_schema_error_details,
    upstream_client_error,
    upstream_error,
    vendor_5xx_product_message,
)
from agentcore.llm.http_pool import acquire_llm_http_client, peek_llm_http_client
from agentcore.llm.provider.anthropic_messages import (
    ANTHROPIC_VERSION,
    AnthropicSseAssembler,
    build_messages_payload,
    parse_unary_message,
    usage_from_anthropic,
)
from agentcore.llm.provider.call_budget import provider_retry_ceiling
from agentcore.llm.provider.openai_compatible import (
    _INITIAL_BACKOFF,
    _IO_ATTEMPT_CEILING,
    _MAX_RETRIES,
    _leaf_http_headers,
    _outbound_call_headers,
)
from agentcore.llm.provider.protocol import (
    BACKOFF_MULTIPLIER,
    LLMChunk,
    LLMRequest,
    LLMResponse,
    connect_retry_policy,
)

logger = get_logger(__name__)

_MESSAGES_PATH = "/messages"


class AnthropicMessagesProvider:
    """OpenCode ``/messages`` leaf. Same credentials as the chat/completions sibling."""

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        extra_headers: dict[str, str] | None = None,
        display_name: str | None = None,
        vendor_id: str = "",
    ) -> None:
        self._name = name
        shown = (display_name or "").strip()
        if shown:
            self._display_name = shown
        elif name == "platform":
            self._display_name = "平台"
        elif name == "user":
            self._display_name = "服务商"
        else:
            self._display_name = name
        self._api_key = require_http_header_safe_api_key(api_key)
        self._base_url = base_url.rstrip("/")
        self._extra_headers = dict(extra_headers) if extra_headers else None
        self._vendor_id = (vendor_id or "").strip()
        self._closed = False
        self._client = acquire_llm_http_client(self._base_url)

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def base_url(self) -> str:
        return self._base_url

    def clone(self) -> AnthropicMessagesProvider:
        return AnthropicMessagesProvider(
            name=self._name,
            api_key=self._api_key,
            base_url=self._base_url,
            extra_headers=self._extra_headers,
            display_name=self._display_name,
            vendor_id=self._vendor_id,
        )

    def _ensure_client_open(self) -> None:
        if self._closed:
            raise LLMClientClosedError()

    def _call_headers(self, payload: dict | None = None) -> dict[str, str]:
        from agentcore.tools.defer_loading import anthropic_tool_search_headers

        headers = {
            **_leaf_http_headers(
                api_key=self._api_key,
                base_url=self._base_url,
                extra=self._extra_headers,
            ),
            **_outbound_call_headers(self._base_url),
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if payload:
            headers.update(anthropic_tool_search_headers(payload))
        return headers

    def _insufficient_balance(self, *, status: int, body: bytes | None) -> LLMError:
        from agentcore.core.errors import LLMInsufficientBalanceError

        message = opencode_credits_product_message(platform=self._name == "platform")
        return LLMInsufficientBalanceError(
            message,
            provider_name=self._name,
            display_name=self._display_name,
            upstream_status=status,
            upstream_body_preview=body_preview(body),
        )

    def _raise_for_status(
        self,
        status_code: int,
        headers,
        *,
        body: bytes | None,
        attempt: int,
        scenario: str,
        payload: dict | None,
        retry_ceiling: float | None,
    ) -> None:
        if status_code < 400:
            return
        if status_code == 429:
            overlay = opencode_typed_rate_limit_message(
                body, platform=self._name == "platform"
            )
            retry_after = headers.get("retry-after") if headers is not None else None
            seconds: float | None = None
            if retry_after:
                try:
                    seconds = float(retry_after)
                except ValueError:
                    seconds = None
            err = upstream_rate_limit_error(
                seconds,
                credential_source="platform" if self._name == "platform" else "user",
                retry_ceiling=retry_ceiling,
            )
            if overlay:
                err.message = overlay
            raise err
        if status_code in (401, 403):
            typed = opencode_typed_client_error(
                body, status=status_code, platform=self._name == "platform"
            )
            if typed is not None:
                raise typed
            if is_balance_exhausted(body):
                raise self._insufficient_balance(status=status_code, body=body)
            if is_auth_rejection(status_code, body):
                from agentcore.core.errors import LLMAuthError

                raise LLMAuthError(
                    provider_name=self._name,
                    display_name=self._display_name,
                    upstream_status=status_code,
                    upstream_body_preview=body_preview(body),
                )
            raise upstream_client_error(
                client_error_message(self._display_name, status_code, body),
                status=status_code,
                body=body,
            )
        if status_code == 402:
            raise self._insufficient_balance(status=status_code, body=body)
        if status_code >= 500:
            logger.warning(
                "llm.upstream_error",
                provider=self._name,
                status_code=status_code,
                attempt=attempt + 1,
                body_preview=body_preview(body),
            )
            envelope = parse_agentcore_error_envelope(body)
            message = vendor_5xx_product_message(
                http_status=status_code,
                relayed=envelope.message if envelope else None,
                envelope=envelope,
            )
            raise upstream_error(
                message,
                status=status_code,
                body=body,
                retry_attempts=attempt,
            )
        if is_non_retryable_client_status(status_code) or 400 <= status_code < 500:
            typed = opencode_typed_client_error(
                body, status=status_code, platform=self._name == "platform"
            )
            if typed is not None:
                raise typed
            raise upstream_client_error(
                client_error_message(self._display_name, status_code, body),
                status=status_code,
                body=body,
                **unsupported_tool_schema_error_details(
                    body, payload=payload, profile=scenario
                ),
            )
        raise LLMError(f"{self._display_name} 调用失败（HTTP {status_code}）")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        payload = build_messages_payload(
            request, stream=False, vendor_id=self._vendor_id
        )
        start = time.monotonic()
        data = await self._request_with_retry(
            payload, scenario=request.scenario, patience=request.retry_patience_seconds
        )
        parsed = parse_unary_message(data)
        usage_raw = data.get("usage")
        usage = usage_from_anthropic(usage_raw if isinstance(usage_raw, dict) else None)
        return LLMResponse(
            content=parsed.content,
            reasoning_content=parsed.reasoning,
            tool_calls=parsed.tool_calls,
            usage=usage,
            finish_reason=parsed.finish_reason,  # type: ignore[arg-type]
            model=str(data.get("model") or request.model),
            latency_ms=int((time.monotonic() - start) * 1000),
            thinking_blocks=parsed.thinking_blocks,
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        payload = build_messages_payload(
            request, stream=True, vendor_id=self._vendor_id
        )
        last_error: Exception | None = None
        backoff = _INITIAL_BACKOFF
        connect_max, connect_backoff = connect_retry_policy(request.scenario)
        started = time.monotonic()
        yielded = False
        self._ensure_client_open()
        for attempt in range(_IO_ATTEMPT_CEILING):
            raise_if_task_cancelled()
            ceiling = provider_retry_ceiling(
                scenario=request.scenario,
                patience=request.retry_patience_seconds,
                elapsed=time.monotonic() - started,
            )
            assembler = AnthropicSseAssembler()
            in_flight: httpx.Response | None = None
            try:
                self._ensure_client_open()
                async with self._client.stream(
                    "POST",
                    _MESSAGES_PATH,
                    json=payload,
                    headers=self._call_headers(payload) or None,
                ) as response:
                    in_flight = response
                    body = await response.aread() if response.status_code >= 400 else None
                    self._raise_for_status(
                        response.status_code,
                        response.headers,
                        body=body,
                        attempt=attempt,
                        scenario=request.scenario,
                        payload=payload,
                        retry_ceiling=ceiling,
                    )
                    async for line in response.aiter_lines():
                        for chunk in assembler.feed_line(line):
                            if chunk.delta_content or chunk.delta_tool_calls:
                                yielded = True
                            yield chunk
                    if assembler.stop_reason is None and not yielded:
                        yield LLMChunk(finish_reason="stop")
                    return
            except asyncio.CancelledError:
                if in_flight is not None:
                    await abort_httpx_response(in_flight)
                raise
            except LLMUpstreamError as e:
                last_error = e
                if yielded or not e.retryable or attempt + 1 >= _MAX_RETRIES:
                    raise
                logger.info(
                    "llm.call_retried",
                    provider=self._name,
                    attempt=attempt + 1,
                    wait_sec=backoff,
                    stream=True,
                    reason=f"upstream_{e.details.get('upstream_status', 500)}",
                )
                await asyncio.sleep(backoff)
                backoff *= BACKOFF_MULTIPLIER
            except LLMError:
                raise
            except httpx.TimeoutException as e:
                raise_if_task_cancelled(e)
                last_error = LLMTimeoutError(f"连接 {self._display_name} 超时，请检查网络后重试")
                is_connect = isinstance(e, httpx.ConnectTimeout)
                max_attempts = connect_max if is_connect else _MAX_RETRIES
                if yielded or attempt + 1 >= max_attempts:
                    raise last_error from e
                wait = connect_backoff if is_connect else backoff
                await asyncio.sleep(wait)
                if is_connect:
                    connect_backoff *= BACKOFF_MULTIPLIER
                else:
                    backoff *= BACKOFF_MULTIPLIER
            except httpx.HTTPError as e:
                raise_if_task_cancelled(e)
                if is_llm_client_closed_error(e):
                    raise LLMClientClosedError() from e
                last_error = LLMError(f"{self._display_name} 网络错误，请检查网络后重试")
                if yielded or attempt + 1 >= _MAX_RETRIES:
                    raise last_error from e
                await asyncio.sleep(backoff)
                backoff *= BACKOFF_MULTIPLIER
        raise last_error or LLMError(f"{self._display_name} 多次重试后仍失败，请稍后重试")

    async def _request_with_retry(
        self, payload: dict, *, scenario: str, patience: float | None
    ) -> dict:
        last_error: Exception | None = None
        backoff = _INITIAL_BACKOFF
        started = time.monotonic()
        self._ensure_client_open()
        for attempt in range(_IO_ATTEMPT_CEILING):
            raise_if_task_cancelled()
            ceiling = provider_retry_ceiling(
                scenario=scenario, patience=patience, elapsed=time.monotonic() - started
            )
            try:
                self._ensure_client_open()
                response = await self._client.post(
                    _MESSAGES_PATH,
                    json=payload,
                    headers=self._call_headers(payload) or None,
                )
                body = response.content if response.status_code >= 400 else None
                self._raise_for_status(
                    response.status_code,
                    response.headers,
                    body=body,
                    attempt=attempt,
                    scenario=scenario,
                    payload=payload,
                    retry_ceiling=ceiling,
                )
                try:
                    data = response.json()
                except ValueError as e:
                    raise LLMInvalidResponseError(
                        f"{self._display_name} 响应格式无效"
                    ) from e
                if not isinstance(data, dict):
                    raise LLMInvalidResponseError(f"{self._display_name} 响应格式无效")
                return data
            except LLMUpstreamError as e:
                last_error = e
                if not e.retryable or attempt + 1 >= _MAX_RETRIES:
                    raise
                await asyncio.sleep(backoff)
                backoff *= BACKOFF_MULTIPLIER
            except LLMError:
                raise
            except httpx.TimeoutException as e:
                raise_if_task_cancelled(e)
                last_error = LLMTimeoutError(f"连接 {self._display_name} 超时，请检查网络后重试")
                if attempt + 1 >= _MAX_RETRIES:
                    raise last_error from e
                await asyncio.sleep(backoff)
                backoff *= BACKOFF_MULTIPLIER
            except httpx.HTTPError as e:
                raise_if_task_cancelled(e)
                last_error = LLMError(f"{self._display_name} 网络错误，请检查网络后重试")
                if attempt + 1 >= _MAX_RETRIES:
                    raise last_error from e
                await asyncio.sleep(backoff)
                backoff *= BACKOFF_MULTIPLIER
        raise last_error or LLMError(f"{self._display_name} 多次重试后仍失败，请稍后重试")

    async def probe(self, *, model: str) -> None:
        payload = {
            "model": model,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
        }
        try:
            response = await self._client.post(
                _MESSAGES_PATH,
                json=payload,
                headers=self._call_headers() or None,
            )
        except httpx.HTTPError as e:
            raise_if_task_cancelled(e)
            raise LLMError(f"{self._display_name} 无法连接，请检查网络后重试") from e
        if response.status_code == 429:
            return
        if response.status_code < 300:
            return
        self._raise_for_status(
            response.status_code,
            response.headers,
            body=response.content,
            attempt=0,
            scenario="probe",
            payload=payload,
            retry_ceiling=None,
        )

    async def probe_tools(self, *, model: str) -> bool | None:
        payload = {
            "model": model,
            "max_tokens": 256,
            "messages": [{"role": "user", "content": "ping"}],
            "tools": [
                {
                    "name": "noop",
                    "description": "probe",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
            "stream": False,
        }
        try:
            response = await self._client.post(
                _MESSAGES_PATH,
                json=payload,
                headers=self._call_headers() or None,
            )
        except httpx.HTTPError:
            return None
        if response.status_code < 300 or response.status_code == 429:
            return True
        if 400 <= response.status_code < 500 and response.status_code not in (401, 402, 403, 404):
            return False
        return None

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.get(
                "/models",
                headers={
                    **_leaf_http_headers(
                        api_key=self._api_key,
                        base_url=self._base_url,
                        extra=self._extra_headers,
                    ),
                    "anthropic-version": ANTHROPIC_VERSION,
                },
            )
        except httpx.HTTPError:
            return []
        if response.status_code >= 400:
            return []
        try:
            data = response.json()
        except ValueError:
            return []
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        ids: list[str] = []
        seen: set[str] = set()
        for row in rows:
            mid = row.get("id") if isinstance(row, dict) else None
            if isinstance(mid, str) and mid and mid not in seen:
                seen.add(mid)
                ids.append(mid)
        return ids

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        client = self._client
        pooled = peek_llm_http_client(self._base_url)
        if client is pooled:
            return
        try:
            if not client.is_closed:
                await client.aclose()
        except Exception:  # noqa: BLE001 — test mock / already-closed transport
            return
