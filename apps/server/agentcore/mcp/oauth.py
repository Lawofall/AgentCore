"""MCP OAuth 2.1 (PKCE + optional DCR) against a remote Streamable HTTP Server."""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import httpx

from agentcore.core.logging import get_logger
from agentcore.core.net import (
    PinnedIPTransport,
    classify_url,
    describe_net_error,
    outbound_async_client,
    web_timeout,
)
from agentcore.mcp.http import McpHttpError, origin_of

logger = get_logger(__name__)

_OAUTH_TIMEOUT = 15.0


def pkce_pair() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` for S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _safe_join(origin: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return urljoin(origin.rstrip("/") + "/", path.lstrip("/"))


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    block = await classify_url(url)
    if block is not None:
        return None
    try:
        response = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def _post_json(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any]
) -> dict[str, Any]:
    block = await classify_url(url)
    if block is not None:
        raise McpHttpError(block.value)
    try:
        response = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise McpHttpError(describe_net_error(exc, url=url)) from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or not isinstance(data, dict):
        raise McpHttpError("OAuth 客户端注册失败")
    return data


async def discover_authorization(mcp_url: str) -> dict[str, str]:
    """Return authorization_endpoint / token_endpoint / registration_endpoint."""
    origin = origin_of(mcp_url)
    if not origin:
        raise McpHttpError("远程地址无效")
    async with outbound_async_client(
        timeout=web_timeout(_OAUTH_TIMEOUT),
        transport=PinnedIPTransport(),
        follow_redirects=True,
    ) as client:
        parsed = urlparse(mcp_url)
        resource_path = parsed.path.rstrip("/")
        candidates = [
            f"{origin}/.well-known/oauth-protected-resource{resource_path}",
            f"{origin}/.well-known/oauth-protected-resource",
        ]
        prm: dict[str, Any] | None = None
        for url in candidates:
            prm = await _get_json(client, url)
            if prm:
                break
        auth_servers = prm.get("authorization_servers") if prm else None
        as_origin = ""
        if isinstance(auth_servers, list) and auth_servers:
            first = str(auth_servers[0] or "").strip()
            as_origin = origin_of(first) or first.rstrip("/")
        if not as_origin:
            as_origin = origin
        metadata = await _get_json(
            client, f"{as_origin}/.well-known/oauth-authorization-server"
        )
        if not metadata:
            metadata = await _get_json(
                client, f"{as_origin}/.well-known/openid-configuration"
            )
        if not metadata:
            raise McpHttpError("该 Server 未公布 OAuth 元数据")
        authorization_endpoint = str(metadata.get("authorization_endpoint") or "").strip()
        token_endpoint = str(metadata.get("token_endpoint") or "").strip()
        registration_endpoint = str(metadata.get("registration_endpoint") or "").strip()
        if not authorization_endpoint or not token_endpoint:
            raise McpHttpError("OAuth 元数据缺少授权 / 令牌端点")
        return {
            "authorization_endpoint": authorization_endpoint
            if authorization_endpoint.startswith("http")
            else _safe_join(as_origin, authorization_endpoint),
            "token_endpoint": token_endpoint
            if token_endpoint.startswith("http")
            else _safe_join(as_origin, token_endpoint),
            "registration_endpoint": (
                registration_endpoint
                if not registration_endpoint
                or registration_endpoint.startswith("http")
                else _safe_join(as_origin, registration_endpoint)
            ),
            "resource": mcp_url,
        }


async def register_client(
    registration_endpoint: str, *, redirect_uri: str
) -> tuple[str, str | None]:
    """Dynamic client registration; returns ``(client_id, client_secret|None)``."""
    async with outbound_async_client(
        timeout=web_timeout(_OAUTH_TIMEOUT),
        transport=PinnedIPTransport(),
        follow_redirects=True,
    ) as client:
        data = await _post_json(
            client,
            registration_endpoint,
            {
                "client_name": "AgentCore",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
    client_id = str(data.get("client_id") or "").strip()
    if not client_id:
        raise McpHttpError("OAuth 客户端注册未返回 client_id")
    secret = str(data.get("client_secret") or "").strip() or None
    return client_id, secret


def authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    challenge: str,
    resource: str,
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource,
        }
    )
    sep = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{sep}{query}"


async def exchange_code(
    *,
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    code_verifier: str,
    client_secret: str | None,
    resource: str,
) -> dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
        "resource": resource,
    }
    headers = {"Accept": "application/json"}
    if client_secret:
        payload["client_secret"] = client_secret
    async with outbound_async_client(
        timeout=web_timeout(_OAUTH_TIMEOUT),
        transport=PinnedIPTransport(),
        follow_redirects=True,
    ) as client:
        block = await classify_url(token_endpoint)
        if block is not None:
            raise McpHttpError(block.value)
        try:
            response = await client.post(token_endpoint, data=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise McpHttpError(describe_net_error(exc, url=token_endpoint)) from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or not isinstance(data, dict) or not data.get(
        "access_token"
    ):
        raise McpHttpError("OAuth 换票失败")
    return data


async def refresh_access_token(
    *,
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
    client_secret: str | None,
    resource: str,
) -> dict[str, Any]:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "resource": resource,
    }
    if client_secret:
        payload["client_secret"] = client_secret
    async with outbound_async_client(
        timeout=web_timeout(_OAUTH_TIMEOUT),
        transport=PinnedIPTransport(),
        follow_redirects=True,
    ) as client:
        block = await classify_url(token_endpoint)
        if block is not None:
            raise McpHttpError(block.value)
        try:
            response = await client.post(token_endpoint, data=payload)
        except httpx.HTTPError as exc:
            raise McpHttpError(describe_net_error(exc, url=token_endpoint)) from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400 or not isinstance(data, dict) or not data.get(
        "access_token"
    ):
        raise McpHttpError("OAuth 刷新失败")
    return data
