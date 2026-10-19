"""Account catalog + handshake for remote MCP Servers."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.config import settings
from agentcore.config.auth import dev_cors_origin_regex
from agentcore.core.errors import KeyStorageUnavailableError, NotFoundError, ValidationError
from agentcore.core.logging import get_logger
from agentcore.core.net import classify_url
from agentcore.db.models.mcp import UserMcpOauthPending, UserMcpServer
from agentcore.db.repositories.mcp_servers import (
    UserMcpOauthPendingRepository,
    UserMcpServerRepository,
)
from agentcore.mcp.http import McpHttpError, StreamableHttpMcpClient
from agentcore.mcp.oauth import (
    authorization_url,
    discover_authorization,
    exchange_code,
    pkce_pair,
    refresh_access_token,
    register_client,
)
from agentcore.security.keys import KeyEncryptor

logger = get_logger(__name__)

_OAUTH_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class McpServerView:
    id: str
    name: str
    url: str
    auth_kind: str
    status: str
    has_token: bool
    last_error: str
    created_at: datetime
    updated_at: datetime


def _encryptor() -> KeyEncryptor | None:
    key = (settings.encryption_key or "").strip()
    if not key:
        return None
    try:
        return KeyEncryptor(key)
    except ValueError:
        return None


def _require_encryptor() -> KeyEncryptor:
    enc = _encryptor()
    if enc is None:
        raise KeyStorageUnavailableError(
            "凭据加密密钥未配置，无法保存 MCP 令牌。请联系管理员。"
        )
    return enc


def _pack_tokens(payload: dict[str, Any]) -> bytes:
    return _require_encryptor().encrypt(json.dumps(payload).encode("utf-8"))


def _unpack_tokens(blob: bytes | None) -> dict[str, Any]:
    if not blob:
        return {}
    enc = _encryptor()
    if enc is None:
        return {}
    try:
        data = json.loads(enc.decrypt(blob).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


async def _validate_url(url: str) -> str:
    raw = url.strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("请填写 http(s) 地址")
    block = await classify_url(raw)
    if block is not None:
        raise ValidationError(block.value)
    return raw


def safe_return_origin(candidate: str, *, fallback: str) -> str:
    """OAuth 回跳只回 CORS 白名单里的源，避免开放重定向。"""
    raw = (candidate or "").strip().rstrip("/")
    home = (fallback or "").strip().rstrip("/")
    if not raw:
        return home
    allowed = {home, *{item.rstrip("/") for item in settings.cors_origins}}
    if raw in allowed:
        return raw
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc or parsed.scheme == "javascript":
        return home
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin in allowed:
        return origin
    pattern = dev_cors_origin_regex(debug=settings.debug)
    if pattern and re.fullmatch(pattern, origin):
        return origin
    return home


def _view(row: UserMcpServer) -> McpServerView:
    return McpServerView(
        id=row.id,
        name=row.name,
        url=row.url,
        auth_kind=row.auth_kind,
        status=row.status,
        has_token=bool(row.token_enc),
        last_error=row.last_error or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _auth_header(tokens: dict[str, Any], *, auth_kind: str) -> str | None:
    access = str(tokens.get("access_token") or "").strip()
    if not access:
        return None
    token_type = str(tokens.get("token_type") or "Bearer").strip() or "Bearer"
    if auth_kind == "bearer" and " " not in access:
        return f"Bearer {access}"
    if access.lower().startswith("bearer "):
        return access
    return f"{token_type} {access}"


class McpServerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = UserMcpServerRepository(session)
        self._pending = UserMcpOauthPendingRepository(session)

    async def list_servers(self, user_id: str) -> list[McpServerView]:
        rows = await self._repo.list_for_user(user_id)
        return [_view(row) for row in rows]

    async def create(
        self,
        user_id: str,
        *,
        name: str,
        url: str,
        bearer: str | None,
    ) -> McpServerView:
        url = await _validate_url(url)
        label = (name or "").strip() or urlparse(url).netloc or "MCP"
        token_enc = None
        auth_kind = "none"
        token = (bearer or "").strip()
        if token:
            auth_kind = "bearer"
            token_enc = _pack_tokens({"access_token": token, "token_type": "Bearer"})
        row = await self._repo.create(
            user_id=user_id,
            name=label[:100],
            url=url,
            auth_kind=auth_kind,
            token_enc=token_enc,
        )
        from agentcore.mcp.remote import clear_remote_mcp_cache

        clear_remote_mcp_cache(user_id)
        logger.info("mcp.server_upserted", user_id=user_id, server_id=row.id)
        return _view(row)

    async def update(
        self,
        user_id: str,
        server_id: str,
        *,
        name: str | None,
        url: str | None,
        bearer: str | None,
        fields_set: set[str],
    ) -> McpServerView:
        row = await self._repo.get(server_id, user_id=user_id)
        if row is None:
            raise NotFoundError("MCP Server 不存在")
        if name is not None:
            row.name = name.strip()[:100] or row.name
        if url is not None:
            row.url = await _validate_url(url)
        if "bearer" in fields_set:
            token = (bearer or "").strip()
            if token:
                row.auth_kind = "bearer"
                row.token_enc = _pack_tokens(
                    {"access_token": token, "token_type": "Bearer"}
                )
            elif bearer == "":
                if row.auth_kind == "bearer":
                    row.auth_kind = "none"
                    row.token_enc = None
        row.status = "unchecked"
        row.last_error = ""
        await self._session.commit()
        from agentcore.mcp.remote import clear_remote_mcp_cache

        clear_remote_mcp_cache(user_id)
        logger.info("mcp.server_upserted", user_id=user_id, server_id=row.id)
        return _view(row)

    async def delete(self, user_id: str, server_id: str) -> None:
        ok = await self._repo.delete(server_id, user_id=user_id)
        if not ok:
            raise NotFoundError("MCP Server 不存在")
        from agentcore.mcp.remote import clear_remote_mcp_cache

        clear_remote_mcp_cache(user_id)
        logger.info("mcp.server_deleted", user_id=user_id, server_id=server_id)

    async def test(self, user_id: str, server_id: str) -> tuple[McpServerView, list[str]]:
        row = await self._repo.get(server_id, user_id=user_id)
        if row is None:
            raise NotFoundError("MCP Server 不存在")
        tools, error = await handshake_server(row)
        row.status = "ready" if not error else (
            "needs_auth" if error == "needs_auth" else "failed"
        )
        row.last_error = "" if not error else (
            "需要登录" if error == "needs_auth" else error[:2000]
        )
        await self._session.commit()
        from agentcore.mcp.remote import clear_remote_mcp_cache

        clear_remote_mcp_cache(user_id)
        names = [
            str(t.get("name") or "").strip()
            for t in tools
            if str(t.get("name") or "").strip()
        ]
        return _view(row), names

    async def start_oauth(
        self,
        user_id: str,
        server_id: str,
        *,
        redirect_uri: str,
        return_origin: str,
    ) -> str:
        row = await self._repo.get(server_id, user_id=user_id)
        if row is None:
            raise NotFoundError("MCP Server 不存在")
        await self._pending.purge_expired()
        meta = await discover_authorization(row.url)
        client_id = (row.oauth_client_id or "").strip()
        client_secret: str | None = None
        stored = _unpack_tokens(row.token_enc)
        if stored.get("client_id"):
            client_id = str(stored["client_id"])
            secret = str(stored.get("client_secret") or "").strip()
            client_secret = secret or None
        if not client_id:
            registration = meta.get("registration_endpoint") or ""
            if not registration:
                raise ValidationError("该 Server 需要登录，但未提供客户端注册")
            client_id, client_secret = await register_client(
                registration, redirect_uri=redirect_uri
            )
            row.oauth_client_id = client_id
            blob = dict(stored)
            blob["client_id"] = client_id
            if client_secret:
                blob["client_secret"] = client_secret
            row.token_enc = _pack_tokens(blob)
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        secret_enc = (
            _require_encryptor().encrypt(client_secret.encode()) if client_secret else None
        )
        await self._pending.put(
            UserMcpOauthPending(
                state=state,
                user_id=user_id,
                server_id=row.id,
                code_verifier=verifier,
                redirect_uri=redirect_uri,
                token_endpoint=meta["token_endpoint"],
                client_id=client_id,
                client_secret_enc=secret_enc,
                return_origin=safe_return_origin(return_origin, fallback="")[:500],
                expires_at=datetime.now(UTC) + _OAUTH_TTL,
            )
        )
        row.auth_kind = "oauth"
        row.oauth_metadata = json.dumps(
            {"authorization_endpoint": meta["authorization_endpoint"],
             "token_endpoint": meta["token_endpoint"],
             "resource": meta["resource"]},
            ensure_ascii=False,
        )
        await self._session.commit()
        logger.info("mcp.oauth_started", user_id=user_id, server_id=row.id)
        return authorization_url(
            authorization_endpoint=meta["authorization_endpoint"],
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            challenge=challenge,
            resource=meta["resource"],
        )

    async def finish_oauth(self, *, code: str, state: str) -> tuple[str, str]:
        await self._pending.purge_expired()
        pending = await self._pending.take(state)
        if pending is None or pending.expires_at <= datetime.now(UTC):
            raise ValidationError("登录已过期，请重试")
        row = await self._repo.get(pending.server_id, user_id=pending.user_id)
        if row is None:
            raise NotFoundError("MCP Server 不存在")
        secret = None
        if pending.client_secret_enc:
            enc = _encryptor()
            if enc is not None:
                try:
                    secret = enc.decrypt(pending.client_secret_enc).decode()
                except Exception:  # noqa: BLE001
                    secret = None
        resource = row.url
        try:
            meta = json.loads(row.oauth_metadata or "{}")
            if isinstance(meta, dict) and meta.get("resource"):
                resource = str(meta["resource"])
        except json.JSONDecodeError:
            pass
        tokens = await exchange_code(
            token_endpoint=pending.token_endpoint,
            code=code,
            redirect_uri=pending.redirect_uri,
            client_id=pending.client_id,
            code_verifier=pending.code_verifier,
            client_secret=secret,
            resource=resource,
        )
        blob = _unpack_tokens(row.token_enc)
        blob.update(
            {
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token") or blob.get("refresh_token"),
                "token_type": tokens.get("token_type") or "Bearer",
                "client_id": pending.client_id,
            }
        )
        if secret:
            blob["client_secret"] = secret
        row.token_enc = _pack_tokens(blob)
        row.auth_kind = "oauth"
        row.status = "unchecked"
        row.last_error = ""
        await self._session.commit()
        from agentcore.mcp.remote import clear_remote_mcp_cache

        clear_remote_mcp_cache(pending.user_id)
        logger.info(
            "mcp.oauth_completed",
            user_id=pending.user_id,
            server_id=row.id,
        )
        return pending.return_origin, row.id


async def handshake_server(row: UserMcpServer) -> tuple[list[dict[str, Any]], str]:
    tokens = _unpack_tokens(row.token_enc)
    header = _auth_header(tokens, auth_kind=row.auth_kind)
    client = StreamableHttpMcpClient(row.url, authorization=header)
    try:
        await client.initialize()
        tools = await client.list_tools()
    except McpHttpError as exc:
        detail = str(exc)
        if detail == "needs_auth" and row.auth_kind == "oauth":
            refreshed = await _try_refresh(row, tokens)
            if refreshed is not None:
                client2 = StreamableHttpMcpClient(
                    row.url, authorization=_auth_header(refreshed, auth_kind="oauth")
                )
                try:
                    await client2.initialize()
                    return await client2.list_tools(), ""
                except McpHttpError as inner:
                    return [], str(inner)
                finally:
                    await client2.aclose()
        return [], detail
    finally:
        await client.aclose()
    return tools, ""


async def _try_refresh(
    row: UserMcpServer, tokens: dict[str, Any]
) -> dict[str, Any] | None:
    refresh = str(tokens.get("refresh_token") or "").strip()
    if not refresh:
        return None
    token_endpoint = ""
    resource = row.url
    try:
        meta = json.loads(row.oauth_metadata or "{}")
        if isinstance(meta, dict):
            token_endpoint = str(meta.get("token_endpoint") or "")
            resource = str(meta.get("resource") or resource)
    except json.JSONDecodeError:
        pass
    if not token_endpoint:
        return None
    try:
        nxt = await refresh_access_token(
            token_endpoint=token_endpoint,
            refresh_token=refresh,
            client_id=str(tokens.get("client_id") or row.oauth_client_id),
            client_secret=str(tokens.get("client_secret") or "") or None,
            resource=resource,
        )
    except McpHttpError:
        return None
    blob = dict(tokens)
    blob.update(
        {
            "access_token": nxt.get("access_token"),
            "refresh_token": nxt.get("refresh_token") or refresh,
            "token_type": nxt.get("token_type") or "Bearer",
        }
    )
    row.token_enc = _pack_tokens(blob)
    return blob


async def list_remote_tools(
    session: AsyncSession, user_id: str
) -> list[tuple[UserMcpServer, list[dict[str, Any]]]]:
    """Handshake account servers; skip failures so chat still starts."""
    rows = await UserMcpServerRepository(session).list_for_user(user_id)
    out: list[tuple[UserMcpServer, list[dict[str, Any]]]] = []
    ready = 0
    failed = 0
    for row in rows:
        tools, error = await handshake_server(row)
        if error:
            failed += 1
            logger.info(
                "mcp.remote_list_degraded",
                server_id=row.id,
                detail=error[:200],
                duration_ms=0,
                tool_count=0,
                failed_servers=1,
            )
            continue
        ready += 1
        out.append((row, tools))
    logger.info(
        "mcp.remote_list_ok",
        duration_ms=0,
        tool_count=sum(len(tools) for _, tools in out),
        ready_servers=ready,
        failed_servers=failed,
    )
    return out


async def call_remote_tool(
    user_id: str,
    *,
    server_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    roots: tuple[dict[str, str], ...] = (),
) -> dict[str, Any]:
    from agentcore.db.base import async_session_factory

    async with async_session_factory() as session:
        row = await UserMcpServerRepository(session).get(server_id, user_id=user_id)
        if row is None:
            raise McpHttpError("MCP Server 未启用或不存在")
        tokens = _unpack_tokens(row.token_enc)
        header = _auth_header(tokens, auth_kind=row.auth_kind)
        client = StreamableHttpMcpClient(row.url, authorization=header, roots=roots)
        try:
            await client.initialize()
            logger.info(
                "mcp.remote_call",
                server_id=row.id,
                tool=tool_name,
            )
            return await client.call_tool(tool_name, arguments)
        except McpHttpError as exc:
            if str(exc) == "needs_auth" and row.auth_kind == "oauth":
                refreshed = await _try_refresh(row, tokens)
                if refreshed is not None:
                    await session.commit()
                    client2 = StreamableHttpMcpClient(
                        row.url,
                        authorization=_auth_header(refreshed, auth_kind="oauth"),
                        roots=roots,
                    )
                    try:
                        await client2.initialize()
                        return await client2.call_tool(tool_name, arguments)
                    finally:
                        await client2.aclose()
            raise
        finally:
            await client.aclose()
