"""Sidecar call to cloud ``/v1/inference/web_search``.

Desktop turns bind the round's inference JWT into a ContextVar. Platform search
and DeepSeek POST that route directly (the count and the key stay on the
server). Sidecar holds no search API key. Cloud API processes never bind the
ContextVar.

Leaf-layer auth is intentionally not ``LLMCredentials`` — web tools must not
import ``agentcore.llm``. Sidecar maps turn credentials into
:class:`InferenceSearchCredentials` before binding.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import httpx

from agentcore.core.net import (
    SEARCH_TIMEOUT,
    WEB_CONNECT_TIMEOUT,
    EgressError,
    outbound_async_client,
)
from agentcore.tools.builtin.web.search_backend import (
    DEFAULT_MAX_RESULTS,
    PhaseCallback,
    SearchResult,
    _parse_results,
)


@dataclass(frozen=True)
class InferenceSearchCredentials:
    """Minimal auth for cloud ``/v1/inference/web_search`` (leaf-owned; no llm deps)."""

    api_key: str
    base_url: str
    extra_headers: dict[str, str] | None = None


_inference_creds: ContextVar[InferenceSearchCredentials | None] = ContextVar(
    "web_search_inference_creds", default=None
)


def bind_inference_search_credentials(
    creds: InferenceSearchCredentials | None,
) -> Token[InferenceSearchCredentials | None]:
    """Install this turn's inference creds for cloud web_search."""
    return _inference_creds.set(creds)


def reset_inference_search_credentials(token: Token[InferenceSearchCredentials | None]) -> None:
    _inference_creds.reset(token)


def get_inference_search_credentials() -> InferenceSearchCredentials | None:
    return _inference_creds.get()


@contextmanager
def inference_search_credentials_scope(
    creds: InferenceSearchCredentials | None,
) -> Iterator[None]:
    """Sidecar turn entry: set creds for the turn tree; always reset on exit."""
    token = bind_inference_search_credentials(creds)
    try:
        yield
    finally:
        reset_inference_search_credentials(token)


def inference_web_search_url(base_url: str) -> str:
    """Derive ``POST …/v1/inference/web_search`` from inference ``base_url``.

    Sidecar ``base_url`` is shaped ``{origin}/v1/inference/v1`` (OpenAI-compat
    chat path). Strip the trailing ``/v1`` and append ``/web_search``.
    """
    u = (base_url or "").strip().rstrip("/")
    if not u:
        raise ValueError("empty inference base_url")
    if u.endswith("/v1"):
        u = u[: -len("/v1")]
    return f"{u}/web_search"


async def cloud_inference_web_search(
    creds: InferenceSearchCredentials,
    query: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
    language: str | None = None,
    on_phase: PhaseCallback | None = None,
) -> list[SearchResult]:
    """POST cloud ``/v1/inference/web_search`` with the turn's inference JWT."""
    url = inference_web_search_url(creds.base_url)
    if on_phase:
        on_phase("querying")
    payload: dict[str, Any] = {
        "query": query,
        "max_results": max(1, min(int(max_results), 12)),
    }
    if language:
        payload["language"] = language
    headers = {
        "Authorization": f"Bearer {creds.api_key}",
        "Accept": "application/json",
        **(creds.extra_headers or {}),
    }
    async with outbound_async_client(
        timeout=httpx.Timeout(SEARCH_TIMEOUT, connect=WEB_CONNECT_TIMEOUT)
    ) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise EgressError(_cloud_search_error_message(resp))
        data = resp.json()
    if not isinstance(data, dict):
        return []
    return _parse_results(data, max_results)


def _cloud_search_error_message(resp: httpx.Response) -> str:
    """Prefer the server's short ``error.message``. Fall back to a generic line."""
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            message = err.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
    return "云端搜索暂时不可用"
