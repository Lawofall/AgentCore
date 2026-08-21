"""Sidecar account narrow-ticket credentials (ContextVar + cloud HTTP client).

Desktop injects ``accountAuth: {baseUrl, apiKey}`` shaped like folders/inference.
``baseUrl`` is the account API root (``…/v1/account``); ``apiKey`` is the
``type=account`` JWT from ``POST /v1/account/token``. Cloud API processes never
bind the ContextVar → conversation-log / rules / memory keep the in-process DB path.
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
    """POST ``…/account/rules/list`` → always + on_demand rule docs.

    Shape: ``{global_rules, project_rules, ancestor_rules, global_on_demand_rules,
    project_on_demand_rules, ancestor_on_demand_rules, folder_chain}``. The ``ancestor_*``
    lists are outermost-first and ``folder_chain`` ends at ``folder_id`` (§5.4 沿树继承);
    on_demand / ancestor / chain keys may be absent on older clouds — treat as empty, which
    degrades to「不继承」rather than to a wrong chain.
    """
    return await _post_json(
        creds,
        path="/rules/list",
        payload={"folder_id": folder_id},
        op="rules_list",
    )


async def cloud_remember_rule(
    creds: AccountCredentials,
    *,
    content: str | None = None,
    folder_id: str | None,
    action: str = "add",
    replaces: str | None = None,
) -> dict[str, Any]:
    """POST ``…/account/rules/remember`` → structured mutate result (changed/action/message/…)."""
    payload: dict[str, Any] = {
        "folder_id": folder_id,
        "action": action or "add",
    }
    if content is not None:
        payload["content"] = content
    if replaces is not None:
        payload["replaces"] = replaces
    data = await _post_json(
        creds,
        path="/rules/remember",
        payload=payload,
        op="rules_remember",
    )
    if not isinstance(data, dict):
        raise AccountCloudError("account remember response is not an object")
    return {
        "changed": bool(data.get("changed")),
        "action": str(data.get("action") or action or "add"),
        "message": str(data.get("message") or ""),
        "rules_markdown": data.get("rules_markdown")
        if isinstance(data.get("rules_markdown"), str) or data.get("rules_markdown") is None
        else str(data.get("rules_markdown")),
    }


async def cloud_memory_list(
    creds: AccountCredentials,
    *,
    scope: str | None,
) -> list[dict[str, Any]]:
    """POST ``…/account/memory/list`` → ``[{path, version, description, disputed}, …]``.

    Items pass through verbatim; an older cloud simply omits the two newer keys (readers
    default them to ""/False, i.e. no description and not disputed).
    """
    data = await _post_json(
        creds,
        path="/memory/list",
        payload={"scope": scope},
        op="memory_list",
    )
    files = data.get("files")
    if not isinstance(files, list):
        raise AccountCloudError("account memory list response missing files[]")
    out: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            raise AccountCloudError("account memory list item is not an object")
        out.append(item)
    return out


async def cloud_memory_load(
    creds: AccountCredentials,
    *,
    path: str,
    scope: str | None,
) -> str:
    """POST ``…/account/memory/load`` → markdown body (empty when missing)."""
    data = await _post_json(
        creds,
        path="/memory/load",
        payload={"path": path, "scope": scope},
        op="memory_load",
    )
    content = data.get("content")
    return content if isinstance(content, str) else ""


async def cloud_memory_save(
    creds: AccountCredentials,
    *,
    path: str,
    content: str,
    scope: str | None,
) -> None:
    """POST ``…/account/memory/save`` — raises on failure (no soft success)."""
    await _post_json(
        creds,
        path="/memory/save",
        payload={"path": path, "content": content, "scope": scope},
        op="memory_save",
    )


async def cloud_memory_delete(
    creds: AccountCredentials,
    *,
    path: str,
    scope: str | None,
) -> None:
    """POST ``…/account/memory/delete`` — raises on failure."""
    await _post_json(
        creds,
        path="/memory/delete",
        payload={"path": path, "scope": scope},
        op="memory_delete",
    )


async def cloud_memory_project_scopes(creds: AccountCredentials) -> list[str]:
    """POST ``…/account/memory/project-scopes`` → folder_id list."""
    data = await _post_json(
        creds,
        path="/memory/project-scopes",
        payload={},
        op="memory_project_scopes",
    )
    scopes = data.get("scopes")
    if not isinstance(scopes, list):
        raise AccountCloudError("account memory project-scopes missing scopes[]")
    return [str(s) for s in scopes if s]


async def cloud_memory_episode_append(
    creds: AccountCredentials,
    *,
    scope: str | None,
    conversation_id: str,
    summary: str,
    actions_json: str = "",
    episode_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """POST ``…/account/memory/episodes/append`` → episode record dict."""
    payload: dict[str, Any] = {
        "scope": scope,
        "conversation_id": conversation_id,
        "summary": summary,
        "actions_json": actions_json or "",
    }
    if episode_id:
        payload["episode_id"] = episode_id
    if created_at:
        payload["created_at"] = created_at
    data = await _post_json(
        creds, path="/memory/episodes/append", payload=payload, op="memory_episode_append"
    )
    if not isinstance(data, dict):
        raise AccountCloudError("account episode append response is not an object")
    return data


async def cloud_memory_episodes_list_undigested(
    creds: AccountCredentials,
    *,
    scope: str | None,
) -> list[dict[str, Any]]:
    """POST ``…/account/memory/episodes/list-undigested`` → episode dicts."""
    data = await _post_json(
        creds,
        path="/memory/episodes/list-undigested",
        payload={"scope": scope},
        op="memory_episodes_list_undigested",
    )
    episodes = data.get("episodes")
    if not isinstance(episodes, list):
        raise AccountCloudError("account episodes list missing episodes[]")
    return [e for e in episodes if isinstance(e, dict)]


async def cloud_memory_episodes_mark_digested(
    creds: AccountCredentials,
    *,
    scope: str | None,
    episode_ids: list[str],
    consolidated_at: str | None = None,
) -> None:
    """POST ``…/account/memory/episodes/mark-digested``."""
    payload: dict[str, Any] = {
        "scope": scope,
        "episode_ids": list(episode_ids),
    }
    if consolidated_at:
        payload["consolidated_at"] = consolidated_at
    await _post_json(
        creds,
        path="/memory/episodes/mark-digested",
        payload=payload,
        op="memory_episodes_mark_digested",
    )


async def cloud_memory_episodes_purge(
    creds: AccountCredentials,
    *,
    older_than_days: int = 30,
) -> int:
    """POST ``…/account/memory/episodes/purge`` → deleted count."""
    data = await _post_json(
        creds,
        path="/memory/episodes/purge",
        payload={"older_than_days": older_than_days},
        op="memory_episodes_purge",
    )
    return int(data.get("deleted") or 0)


async def cloud_memory_scope_state_get(
    creds: AccountCredentials,
    *,
    scope: str | None,
) -> dict[str, Any]:
    """POST ``…/account/memory/scope-state/get`` → state fields."""
    data = await _post_json(
        creds,
        path="/memory/scope-state/get",
        payload={"scope": scope},
        op="memory_scope_state_get",
    )
    if not isinstance(data, dict):
        raise AccountCloudError("account scope-state get response is not an object")
    return data


async def cloud_memory_scope_state_save(
    creds: AccountCredentials,
    *,
    scope: str | None,
    last_semantic_at: str | None = None,
    explore_workspace_key: str | None = None,
    explore_fingerprint: str | None = None,
    explore_fingerprint_dirty: bool = False,
) -> None:
    """POST ``…/account/memory/scope-state/save``."""
    await _post_json(
        creds,
        path="/memory/scope-state/save",
        payload={
            "scope": scope,
            "last_semantic_at": last_semantic_at,
            "explore_workspace_key": explore_workspace_key,
            "explore_fingerprint": explore_fingerprint,
            "explore_fingerprint_dirty": explore_fingerprint_dirty,
        },
        op="memory_scope_state_save",
    )
