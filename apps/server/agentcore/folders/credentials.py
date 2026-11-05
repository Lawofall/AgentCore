"""Sidecar folders narrow-ticket credentials (ContextVar + cloud HTTP client).

Desktop injects ``{baseUrl, apiKey}`` shaped like inference; ``baseUrl`` is the
folders collection URL (``…/v1/folders``). Cloud API processes never bind the
ContextVar → tools / desk-binding keep the in-process DB path.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import httpx

from agentcore.api.schemas.conversations import FolderSummary
from agentcore.core.logging import get_logger
from agentcore.core.net import WEB_CONNECT_TIMEOUT, outbound_async_client

logger = get_logger(__name__)

_FOLDERS_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=WEB_CONNECT_TIMEOUT)


@dataclass(frozen=True)
class FoldersCredentials:
    """Minimal auth for cloud ``/v1/folders*`` (leaf-owned; no llm deps)."""

    api_key: str
    base_url: str


_folders_creds: ContextVar[FoldersCredentials | None] = ContextVar(
    "folders_cloud_creds", default=None
)


class FoldersCloudError(Exception):
    """Cloud folders HTTP failed (connectivity / auth / unexpected status)."""

    def __init__(self, message: str, *, code: str = "folders_cloud_failed") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def bind_folders_credentials(
    creds: FoldersCredentials | None,
) -> Token[FoldersCredentials | None]:
    """Install this turn's folders creds for cloud roster / desk-binding calls."""
    return _folders_creds.set(creds)


def reset_folders_credentials(token: Token[FoldersCredentials | None]) -> None:
    _folders_creds.reset(token)


def get_folders_credentials() -> FoldersCredentials | None:
    return _folders_creds.get()


@contextmanager
def folders_credentials_scope(
    creds: FoldersCredentials | None,
) -> Iterator[None]:
    """Sidecar turn entry: set creds for the turn tree; always reset on exit."""
    token = bind_folders_credentials(creds)
    try:
        yield
    finally:
        reset_folders_credentials(token)


def _collection_url(base_url: str) -> str:
    u = (base_url or "").strip().rstrip("/")
    if not u:
        raise FoldersCloudError("folders baseUrl empty", code="folders_cloud_config")
    return u


def _auth_headers(creds: FoldersCredentials) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {creds.api_key}",
        "Accept": "application/json",
    }


def _summary_dict(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise FoldersCloudError("folders response is not an object")
    return FolderSummary.model_validate(data).model_dump(mode="json")


def _raise_for_status(resp: httpx.Response, *, op: str) -> None:
    if resp.status_code in (401, 403):
        raise FoldersCloudError(
            f"folders {op} unauthorized ({resp.status_code})",
            code="folders_cloud_unauthorized",
        )
    if resp.status_code >= 500:
        raise FoldersCloudError(
            f"folders {op} server error ({resp.status_code})",
            code="folders_cloud_server",
        )
    if resp.status_code >= 400:
        raise FoldersCloudError(
            f"folders {op} failed ({resp.status_code})",
            code="folders_cloud_failed",
        )


async def cloud_list_folders(creds: FoldersCredentials) -> list[dict[str, Any]]:
    """GET folders collection → FolderSummary-shaped dicts."""
    url = _collection_url(creds.base_url)
    try:
        async with outbound_async_client(timeout=_FOLDERS_HTTP_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers=_auth_headers(creds),
                params={"desk_map": "outline"},
            )
    except httpx.HTTPError as exc:
        logger.warning("folders.cloud_list_failed", error=str(exc))
        raise FoldersCloudError(
            f"folders list unreachable: {exc}",
            code="folders_cloud_unreachable",
        ) from exc
    _raise_for_status(resp, op="list")
    data = resp.json()
    if not isinstance(data, list):
        raise FoldersCloudError("folders list response is not an array")
    return [_summary_dict(item) for item in data]


async def cloud_create_cloud_folder(
    creds: FoldersCredentials, *, name: str
) -> dict[str, Any]:
    """POST folders collection with ``mode=cloud`` → FolderSummary dict."""
    url = _collection_url(creds.base_url)
    payload: dict[str, Any] = {"name": name, "mode": "cloud"}
    try:
        async with outbound_async_client(timeout=_FOLDERS_HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=_auth_headers(creds))
    except httpx.HTTPError as exc:
        logger.warning("folders.cloud_create_failed", error=str(exc))
        raise FoldersCloudError(
            f"folders create unreachable: {exc}",
            code="folders_cloud_unreachable",
        ) from exc
    if resp.status_code not in (200, 201):
        _raise_for_status(resp, op="create")
    return _summary_dict(resp.json())


async def cloud_soft_delete_folder(
    creds: FoldersCredentials, *, folder_id: str
) -> bool:
    """DELETE ``…/folders/{id}`` (软删) → True, or False on 404 (business miss).

    Soft delete only — the ``/permanent`` twin is never reachable from here (it
    stays access-session only, i.e. the desktop confirm dialog).
    """
    fid = (folder_id or "").strip()
    if not fid:
        return False
    url = f"{_collection_url(creds.base_url)}/{fid}"
    try:
        async with outbound_async_client(timeout=_FOLDERS_HTTP_TIMEOUT) as client:
            resp = await client.delete(url, headers=_auth_headers(creds))
    except httpx.HTTPError as exc:
        logger.warning(
            "folders.cloud_delete_failed",
            folder_id=fid,
            error=str(exc),
        )
        raise FoldersCloudError(
            f"folders delete unreachable: {exc}",
            code="folders_cloud_unreachable",
        ) from exc
    if resp.status_code == 404:
        return False
    _raise_for_status(resp, op="delete")
    return True


async def cloud_get_folder(
    creds: FoldersCredentials, *, folder_id: str
) -> dict[str, Any] | None:
    """GET ``…/folders/{id}`` → FolderSummary dict, or None on 404 (business miss)."""
    fid = (folder_id or "").strip()
    if not fid:
        return None
    url = f"{_collection_url(creds.base_url)}/{fid}"
    try:
        async with outbound_async_client(timeout=_FOLDERS_HTTP_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers=_auth_headers(creds),
                params={"desk_map": "map"},
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "folders.cloud_get_failed",
            folder_id=fid,
            error=str(exc),
        )
        raise FoldersCloudError(
            f"folders get unreachable: {exc}",
            code="folders_cloud_unreachable",
        ) from exc
    if resp.status_code == 404:
        return None
    _raise_for_status(resp, op="get")
    return _summary_dict(resp.json())
