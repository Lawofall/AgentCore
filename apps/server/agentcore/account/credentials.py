"""Sidecar account narrow-ticket credentials (ContextVar + cloud HTTP client).

Desktop injects ``accountAuth: {baseUrl, apiKey}`` shaped like folders/inference.
``baseUrl`` is the account API root (``…/v1/account``); ``apiKey`` is the
``type=account`` JWT from ``POST /v1/account/token``. Cloud API processes never
bind the ContextVar → conversation-log / rules / memory / model catalog keep
the in-process DB path. Ticketed turns read the catalog with
``GET {baseUrl}/models``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import httpx

from agentcore.core.logging import get_logger
from agentcore.core.net import WEB_CONNECT_TIMEOUT, outbound_async_client

logger = get_logger(__name__)

_ACCOUNT_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=WEB_CONNECT_TIMEOUT)


@dataclass(frozen=True)
class AccountCredentials:
    """Minimal auth for cloud ``/v1/account/*`` engine surface (leaf-owned)."""

    api_key: str
    base_url: str


_account_creds: ContextVar[AccountCredentials | None] = ContextVar(
    "account_cloud_creds", default=None
)


class AccountCloudError(Exception):
    """Cloud account/conversation-log HTTP failed (connectivity / auth / status)."""

    def __init__(self, message: str, *, code: str = "account_cloud_failed") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def bind_account_credentials(
    creds: AccountCredentials | None,
) -> Token[AccountCredentials | None]:
    """Install this turn's account creds for cloud conversation-log calls."""
    return _account_creds.set(creds)


def reset_account_credentials(token: Token[AccountCredentials | None]) -> None:
    _account_creds.reset(token)


def get_account_credentials() -> AccountCredentials | None:
    return _account_creds.get()


@contextmanager
def account_credentials_scope(
    creds: AccountCredentials | None,
) -> Iterator[None]:
    """Sidecar turn entry: set creds for the turn tree; always reset on exit."""
    token = bind_account_credentials(creds)
    try:
        yield
    finally:
        reset_account_credentials(token)


def _root_url(base_url: str) -> str:
    u = (base_url or "").strip().rstrip("/")
    if not u:
        raise AccountCloudError("account baseUrl empty", code="account_cloud_config")
    return u


def _auth_headers(creds: AccountCredentials) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {creds.api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _fastapi_validation_bits(resp: httpx.Response) -> str:
    """Join FastAPI 422 ``detail[]`` loc/msg into a model-readable fragment."""
    try:
        payload = resp.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail")
    if not isinstance(detail, list):
        return ""
    parts: list[str] = []
    for item in detail:
        if not isinstance(item, dict):
            continue
        loc = item.get("loc")
        msg = item.get("msg")
        field = ""
        if isinstance(loc, list):
            field = ".".join(str(x) for x in loc if x != "body")
        msg_s = str(msg).strip() if msg is not None else ""
        if field and msg_s:
            parts.append(f"{field}: {msg_s}")
        elif msg_s:
            parts.append(msg_s)
        elif field:
            parts.append(field)
    return "; ".join(parts)


def _raise_for_status(resp: httpx.Response, *, op: str) -> None:
    if resp.status_code in (401, 403):
        raise AccountCloudError(
            f"account {op} unauthorized ({resp.status_code})",
            code="account_cloud_unauthorized",
        )
    if resp.status_code >= 500:
        raise AccountCloudError(
            f"account {op} server error ({resp.status_code})",
            code="account_cloud_server",
        )
    if resp.status_code == 409:
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict) and detail.get("code") == "ALWAYS_QUOTA_EXCEEDED":
            raise AccountCloudError(
                str(detail.get("message") or "常驻条目配额已满"),
                code="ALWAYS_QUOTA_EXCEEDED",
            )
        raise AccountCloudError(
            f"account {op} failed (409)",
            code="account_cloud_failed",
        )
    if resp.status_code == 422:
        bits = _fastapi_validation_bits(resp)
        message = f"account {op} validation (422)"
        if bits:
            message = f"{message}: {bits}"
        raise AccountCloudError(message, code="account_cloud_validation")
    if resp.status_code >= 400:
        raise AccountCloudError(
            f"account {op} failed ({resp.status_code})",
            code="account_cloud_failed",
        )


async def cloud_search_conversations(
    creds: AccountCredentials,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST ``…/account/conversations/search`` → structured search payload."""
    url = f"{_root_url(creds.base_url)}/conversations/search"
    try:
        async with outbound_async_client(timeout=_ACCOUNT_HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=_auth_headers(creds))
    except httpx.HTTPError as exc:
        logger.warning("account.cloud_search_failed", error=str(exc))
        raise AccountCloudError(
            f"conversation search unreachable: {exc}",
            code="account_cloud_unreachable",
        ) from exc
    _raise_for_status(resp, op="search")
    data = resp.json()
    if not isinstance(data, dict):
        raise AccountCloudError("account search response is not an object")
    return data


async def cloud_chat_context(
    creds: AccountCredentials,
    *,
    conversation_id: str,
) -> dict[str, Any]:
    """POST ``…/account/conversations/chat-context`` → assembled CEO window."""
    url = f"{_root_url(creds.base_url)}/conversations/chat-context"
    try:
        async with outbound_async_client(timeout=_ACCOUNT_HTTP_TIMEOUT) as client:
            resp = await client.post(
                url,
                json={"conversation_id": conversation_id},
                headers=_auth_headers(creds),
            )
    except httpx.HTTPError as exc:
        logger.warning("account.cloud_chat_context_failed", error=str(exc))
        raise AccountCloudError(
            f"conversation chat-context unreachable: {exc}",
            code="account_cloud_unreachable",
        ) from exc
    _raise_for_status(resp, op="chat_context")
    data = resp.json()
    if not isinstance(data, dict):
        raise AccountCloudError("account chat-context response is not an object")
    return data


async def cloud_list_models(creds: AccountCredentials) -> dict[str, Any]:
    """GET ``…/account/models`` → the user's model catalog (same shape as ``/users/me/models``)."""
    url = f"{_root_url(creds.base_url)}/models"
    try:
        async with outbound_async_client(timeout=_ACCOUNT_HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers=_auth_headers(creds))
    except httpx.HTTPError as exc:
        logger.warning("account.cloud_models_failed", error=str(exc))
        raise AccountCloudError(
            f"model catalog unreachable: {exc}",
            code="account_cloud_unreachable",
        ) from exc
    _raise_for_status(resp, op="models")
    data = resp.json()
    if not isinstance(data, dict):
        raise AccountCloudError("account models response is not an object")
    return data


async def cloud_read_conversation(
    creds: AccountCredentials,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST ``…/account/conversations/read`` → structured read payload."""
    url = f"{_root_url(creds.base_url)}/conversations/read"
    try:
        async with outbound_async_client(timeout=_ACCOUNT_HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=_auth_headers(creds))
    except httpx.HTTPError as exc:
        logger.warning("account.cloud_read_failed", error=str(exc))
        raise AccountCloudError(
            f"conversation read unreachable: {exc}",
            code="account_cloud_unreachable",
        ) from exc
    _raise_for_status(resp, op="read")
    data = resp.json()
    if not isinstance(data, dict):
        raise AccountCloudError("account read response is not an object")
    return data


async def _post_json(
    creds: AccountCredentials,
    *,
    path: str,
    payload: dict[str, Any],
    op: str,
) -> dict[str, Any]:
    """POST ``{baseUrl}{path}`` → JSON object; shared by rules/memory clients."""
    url = f"{_root_url(creds.base_url)}{path}"
    try:
        async with outbound_async_client(timeout=_ACCOUNT_HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=_auth_headers(creds))
    except httpx.HTTPError as exc:
        logger.warning(f"account.cloud_{op}_failed", error=str(exc))
        raise AccountCloudError(
            f"account {op} unreachable: {exc}",
            code="account_cloud_unreachable",
        ) from exc
    _raise_for_status(resp, op=op)
    data = resp.json()
    if not isinstance(data, dict):
        raise AccountCloudError(f"account {op} response is not an object")
    return data


async def cloud_list_user_rules(
    creds: AccountCredentials,
    *,
    folder_id: str | None,
) -> dict[str, Any]:
    """POST ``…/account/rules/list`` → always, on_demand, and path rule docs.

    Shape includes ``global_rules`` / ``project_rules`` / ``ancestor_rules``, the matching
    ``*_on_demand_rules`` and ``*_path_rules`` lists, and ``folder_chain``. The ``ancestor_*``
    lists are outermost-first and ``folder_chain`` ends at ``folder_id`` (§5.4 沿树继承);
    on_demand / path / ancestor / chain keys may be absent on older clouds — treat as empty,
    which degrades to「不继承」rather than to a wrong chain.
    """
    return await _post_json(
        creds,
        path="/rules/list",
        payload={"folder_id": folder_id},
        op="rules_list",
    )


async def cloud_write_user_rule(
    creds: AccountCredentials,
    *,
    name: str,
    content: str,
    folder_id: str | None,
    apply: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """POST ``…/account/rules/write`` → structured mutate result."""
    payload: dict[str, Any] = {
        "folder_id": folder_id,
        "name": name,
        "content": content,
    }
    if apply is not None:
        payload["apply"] = apply
    if description is not None:
        payload["description"] = description
    return await _cloud_rule_mutate(creds, path="/rules/write", payload=payload, op="rules_write")


async def cloud_read_user_rule(
    creds: AccountCredentials,
    *,
    name: str,
    folder_id: str | None,
) -> dict[str, Any]:
    """POST ``…/account/rules/read`` → structured mutate result."""
    return await _cloud_rule_mutate(
        creds,
        path="/rules/read",
        payload={"folder_id": folder_id, "name": name},
        op="rules_read",
    )


async def cloud_delete_user_rule(
    creds: AccountCredentials,
    *,
    name: str,
    folder_id: str | None,
) -> dict[str, Any]:
    """POST ``…/account/rules/delete`` → structured mutate result."""
    return await _cloud_rule_mutate(
        creds,
        path="/rules/delete",
        payload={"folder_id": folder_id, "name": name},
        op="rules_delete",
    )


async def _cloud_rule_mutate(
    creds: AccountCredentials,
    *,
    path: str,
    payload: dict[str, Any],
    op: str,
) -> dict[str, Any]:
    data = await _post_json(creds, path=path, payload=payload, op=op)
    if not isinstance(data, dict):
        raise AccountCloudError(f"account {op} response is not an object")
    catalog: list[dict[str, str]] = []
    raw_catalog = data.get("catalog")
    if isinstance(raw_catalog, list):
        for item in raw_catalog:
            if isinstance(item, dict):
                catalog.append(
                    {
                        "name": str(item.get("name") or ""),
                        "apply": str(item.get("apply") or ""),
                        "description": str(item.get("description") or ""),
                    }
                )
    ok_raw = data.get("ok")
    return {
        "changed": bool(data.get("changed")),
        "action": str(data.get("action") or ""),
        "message": str(data.get("message") or ""),
        "name": str(data.get("name") or payload.get("name") or ""),
        "apply": str(data.get("apply") or ""),
        "body": str(data.get("body") or ""),
        "catalog": catalog,
        "ok": True if ok_raw is None else bool(ok_raw),
    }
