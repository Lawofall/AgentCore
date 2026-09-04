"""Folders narrow token + cloud roster path (定案甲步骤 2+3)."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from agentcore.core.errors import AuthenticationError
from agentcore.folders.credentials import (
    FoldersCloudError,
    FoldersCredentials,
    cloud_create_cloud_folder,
    cloud_get_folder,
    cloud_list_folders,
    folders_credentials_scope,
)
from agentcore.runtime.delegate.target_desktop import (
    TargetDesktopError,
    load_target_folder_binding,
)
from agentcore.security import (
    create_access_token,
    create_folders_token,
    create_inference_token,
    decode_access_token,
    decode_folders_token,
    decode_inference_token,
)
from agentcore.tools.builtin.folders import (
    CreateFolderTool,
    ListFoldersTool,
    ResolveFolderTool,
)
from agentcore.tools.protocol import ToolContext

pytestmark = pytest.mark.anyio


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=SimpleNamespace(location="local"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
    )


def _summary(
    *,
    id: str = "f1",
    name: str = "Alpha",
    mode: str = "cloud",
    local_root_id: str | None = None,
    local_subpath: str | None = None,
    rel_path: str | None = None,
) -> dict[str, Any]:
    now = datetime(2026, 8, 9, 12, 0, 0).isoformat()
    return {
        "id": id,
        "name": name,
        "mode": mode,
        "local_root_id": local_root_id,
        "local_subpath": local_subpath,
        "rel_path": rel_path if rel_path is not None else name,
        "owner_user_id": "u1",
        "my_role": "owner",
        "my_state": "accepted",
        "created_at": now,
        "updated_at": now,
    }


# --- token mutual exclusion ---------------------------------------------------


def test_folders_token_roundtrip():
    token = create_folders_token("user-1")
    assert decode_folders_token(token) == "user-1"


def test_folders_token_rejects_access_and_inference():
    access = create_access_token("user-1", audience="product")
    inference = create_inference_token("user-1")
    with pytest.raises(AuthenticationError):
        decode_folders_token(access)
    with pytest.raises(AuthenticationError):
        decode_folders_token(inference)


def test_access_and_inference_reject_folders_token():
    folders = create_folders_token("user-1")
    with pytest.raises(AuthenticationError):
        decode_access_token(folders)
    with pytest.raises(AuthenticationError):
        decode_inference_token(folders)


def test_folders_token_rejects_expired():
    expired = create_folders_token("user-1", expires_delta=timedelta(minutes=-1))
    with pytest.raises(AuthenticationError):
        decode_folders_token(expired)


# --- cloud HTTP client --------------------------------------------------------


class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._handler(request)


@pytest.fixture
def folders_creds() -> FoldersCredentials:
    return FoldersCredentials(
        api_key="folders-jwt",
        base_url="https://cloud.example/v1/folders",
    )


async def test_cloud_list_folders_ok(monkeypatch: pytest.MonkeyPatch, folders_creds):
    async def _handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://cloud.example/v1/folders"
        assert request.headers["Authorization"] == "Bearer folders-jwt"
        return httpx.Response(200, json=[_summary(id="a", name="A")])

    monkeypatch.setattr(
        "agentcore.folders.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    rows = await cloud_list_folders(folders_creds)
    assert len(rows) == 1
    assert rows[0]["id"] == "a"
    assert rows[0]["mode"] == "cloud"


async def test_cloud_create_and_get(monkeypatch: pytest.MonkeyPatch, folders_creds):
    async def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = httpx.Response(201, json=_summary(id="new", name="N"))
            return body
        if request.method == "GET" and str(request.url).endswith("/local-1"):
            return httpx.Response(
                200,
                json=_summary(
                    id="local-1",
                    name="Local",
                    mode="local",
                    local_root_id="root-x",
                    local_subpath="apps",
                ),
            )
        if request.method == "GET" and str(request.url).endswith("/missing"):
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(500, json={"detail": "boom"})

    monkeypatch.setattr(
        "agentcore.folders.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    created = await cloud_create_cloud_folder(folders_creds, name="N")
    assert created["id"] == "new"
    got = await cloud_get_folder(folders_creds, folder_id="local-1")
    assert got is not None
    assert got["local_root_id"] == "root-x"
    assert await cloud_get_folder(folders_creds, folder_id="missing") is None


async def test_cloud_list_unauthorized(monkeypatch: pytest.MonkeyPatch, folders_creds):
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "nope"})

    monkeypatch.setattr(
        "agentcore.folders.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    with pytest.raises(FoldersCloudError) as ei:
        await cloud_list_folders(folders_creds)
    assert ei.value.code == "folders_cloud_unauthorized"


# --- tools: HTTP when creds bound; DB when not --------------------------------


async def test_list_folders_uses_cloud_when_creds_bound(
    monkeypatch: pytest.MonkeyPatch, folders_creds
):
    import agentcore.tools.builtin.folders as folders_mod

    db_called = {"n": 0}

    class _Repo:
        def __init__(self, session: Any) -> None:
            del session
            db_called["n"] += 1

        async def list_by_user(self, user_id: str) -> list:
            del user_id
            raise AssertionError("must not hit DB when folders creds bound")

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(folders_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(folders_mod, "FolderRepository", _Repo)

    async def _fake_list(creds: FoldersCredentials) -> list[dict[str, Any]]:
        assert creds.api_key == "folders-jwt"
        return [_summary(id="cloud-only", name="Via HTTP")]

    monkeypatch.setattr(
        "agentcore.folders.credentials.cloud_list_folders",
        _fake_list,
    )

    with folders_credentials_scope(folders_creds):
        result = await ListFoldersTool().execute({}, _ctx())
    assert result.success
    assert "cloud-only" in result.output
    assert db_called["n"] == 0


async def test_list_folders_uses_db_without_creds(monkeypatch: pytest.MonkeyPatch):
    import agentcore.tools.builtin.folders as folders_mod

    class _Folder:
        id = "db-1"
        name = "FromDB"
        user_id = "u1"
        local_root_id = None
        local_subpath = None
        rel_path = "FromDB"
        created_at = datetime(2026, 8, 9, 12, 0, 0)
        updated_at = datetime(2026, 8, 9, 12, 0, 0)

    class _Repo:
        def __init__(self, session: Any) -> None:
            del session

        async def list_by_user(self, user_id: str) -> list:
            assert user_id == "u1"
            return [_Folder()]

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(folders_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(folders_mod, "FolderRepository", _Repo)

    cloud_list = AsyncMock(side_effect=AssertionError("no cloud without creds"))
    monkeypatch.setattr("agentcore.folders.credentials.cloud_list_folders", cloud_list)

    result = await ListFoldersTool().execute({}, _ctx())
    assert result.success
    assert "FromDB" in result.output
    cloud_list.assert_not_awaited()


async def test_create_folder_cloud_http_path(
    monkeypatch: pytest.MonkeyPatch, folders_creds
):
    import agentcore.tools.builtin.folders as folders_mod

    class _Repo:
        def __init__(self, session: Any) -> None:
            del session

        async def create(self, **kwargs: Any) -> None:
            raise AssertionError(f"must not hit DB: {kwargs}")

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(folders_mod, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(folders_mod, "FolderRepository", _Repo)

    async def _fake_create(
        creds: FoldersCredentials, *, name: str, parent_id: str | None = None
    ) -> dict[str, Any]:
        assert name == "NewCloud"
        assert parent_id is None
        return _summary(id="created-1", name=name)

    monkeypatch.setattr(
        "agentcore.folders.credentials.cloud_create_cloud_folder",
        _fake_create,
    )

    with folders_credentials_scope(folders_creds):
        result = await CreateFolderTool().execute({"name": "NewCloud"}, _ctx())
    assert result.success
    assert "created-1" in result.output


async def test_create_folder_nested_cloud_http_path(
    monkeypatch: pytest.MonkeyPatch, folders_creds
):
    """``parent_path`` resolves off the same cloud roster, then rides HTTP as an id."""

    async def _fake_list(creds: FoldersCredentials) -> list[dict[str, Any]]:
        del creds
        return [_summary(id="parent-1", name="设计", rel_path="工作/设计")]

    async def _fake_create(
        creds: FoldersCredentials, *, name: str, parent_id: str | None = None
    ) -> dict[str, Any]:
        assert parent_id == "parent-1"
        return _summary(id="created-2", name=name, rel_path=f"工作/设计/{name}")

    monkeypatch.setattr("agentcore.folders.credentials.cloud_list_folders", _fake_list)
    monkeypatch.setattr(
        "agentcore.folders.credentials.cloud_create_cloud_folder", _fake_create
    )

    with folders_credentials_scope(folders_creds):
        result = await CreateFolderTool().execute(
            {"name": "图标", "parent_path": "工作/设计"}, _ctx()
        )
    assert result.success
    assert result.display["rel_path"] == "工作/设计/图标"


async def test_resolve_folder_cloud_http_path(
    monkeypatch: pytest.MonkeyPatch, folders_creds
):
    async def _fake_list(creds: FoldersCredentials) -> list[dict[str, Any]]:
        del creds
        return [_summary(id="only", name="Solo")]

    monkeypatch.setattr("agentcore.folders.credentials.cloud_list_folders", _fake_list)

    with folders_credentials_scope(folders_creds):
        result = await ResolveFolderTool().execute({"path": "solo"}, _ctx())
    assert result.success
    assert result.display["folder_id"] == "only"


# --- desk binding via cloud get -----------------------------------------------


async def test_load_target_folder_binding_cloud_get(
    monkeypatch: pytest.MonkeyPatch, folders_creds
):
    async def _fake_get(creds: FoldersCredentials, *, folder_id: str) -> dict[str, Any]:
        assert folder_id == "local-1"
        return _summary(
            id="local-1",
            name="Desk",
            mode="local",
            local_root_id="root-z",
            local_subpath="pkg",
        )

    monkeypatch.setattr("agentcore.folders.credentials.cloud_get_folder", _fake_get)

    with folders_credentials_scope(folders_creds):
        binding = await load_target_folder_binding(folder_id="local-1", user_id="u1")
    assert binding is not None
    assert binding.folder_id == "local-1"
    assert binding.name == "Desk"
    assert binding.local_binding is not None
    assert binding.local_binding.root_id == "root-z"
    assert binding.local_binding.subpath == "pkg"


async def test_load_target_folder_binding_cloud_auth_fails_honestly(
    monkeypatch: pytest.MonkeyPatch, folders_creds
):
    async def _fake_get(creds: FoldersCredentials, *, folder_id: str) -> dict[str, Any]:
        del creds, folder_id
        raise FoldersCloudError("unauthorized", code="folders_cloud_unauthorized")

    monkeypatch.setattr("agentcore.folders.credentials.cloud_get_folder", _fake_get)

    with folders_credentials_scope(folders_creds), pytest.raises(TargetDesktopError) as ei:
        await load_target_folder_binding(folder_id="any", user_id="u1")
    assert "无法绑定目标文件夹" in ei.value.message
    assert "unauthorized" in ei.value.message


async def test_load_target_folder_binding_still_db_without_creds(
    monkeypatch: pytest.MonkeyPatch,
):
    class _Folder:
        id = "db-f"
        name = "DB"
        local_root_id = None
        local_subpath = None
        rel_path = "DB"

    class _Repo:
        def __init__(self, session: object) -> None:
            del session

        async def get_by_id(self, folder_id: str, *, user_id: str) -> _Folder:
            assert folder_id == "db-f"
            assert user_id == "u1"
            return _Folder()

    class _CM:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    import agentcore.db.base as db_base
    import agentcore.db.repositories as repos

    monkeypatch.setattr(db_base, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(repos, "FolderRepository", _Repo)

    cloud_get = AsyncMock(side_effect=AssertionError("no cloud"))
    monkeypatch.setattr("agentcore.folders.credentials.cloud_get_folder", cloud_get)

    binding = await load_target_folder_binding(folder_id="db-f", user_id="u1")
    assert binding is not None
    assert binding.folder_id == "db-f"
    assert binding.local_binding is None
    cloud_get.assert_not_awaited()


async def test_mint_folders_token_response():
    from agentcore.api.routes.folders import mint_folders_token

    user = SimpleNamespace(user_id="u1")
    resp = await mint_folders_token(user)  # type: ignore[arg-type]
    assert resp.expires_in_sec > 0
    assert decode_folders_token(resp.token) == "u1"


async def test_folders_api_user_accepts_folders_bearer(monkeypatch: pytest.MonkeyPatch):
    from agentcore.api import dependencies as deps

    user = SimpleNamespace(user_id="u1", status="active", role="user")

    class _Repo:
        async def get_by_id(self, user_id: str):
            assert user_id == "u1"
            return user

    request = SimpleNamespace(url=SimpleNamespace(path="/v1/folders"), state=SimpleNamespace())
    token = create_folders_token("u1")
    got = await deps.get_folders_api_user(
        request,  # type: ignore[arg-type]
        access_token=None,
        authorization=f"Bearer {token}",
        user_repo=_Repo(),  # type: ignore[arg-type]
    )
    assert got.user_id == "u1"


async def test_folders_api_user_rejects_inference_bearer():
    from agentcore.api import dependencies as deps

    class _Repo:
        async def get_by_id(self, user_id: str):
            raise AssertionError("should not load user")

    request = SimpleNamespace(url=SimpleNamespace(path="/v1/folders"), state=SimpleNamespace())
    token = create_inference_token("u1")
    with pytest.raises(AuthenticationError):
        await deps.get_folders_api_user(
            request,  # type: ignore[arg-type]
            access_token=None,
            authorization=f"Bearer {token}",
            user_repo=_Repo(),  # type: ignore[arg-type]
        )
