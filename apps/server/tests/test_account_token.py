"""Account narrow token + conversation-log cloud path (定案 R3a)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agentcore.account.credentials import (
    AccountCloudError,
    AccountCredentials,
    account_credentials_scope,
    cloud_chat_context,
    cloud_read_conversation,
    cloud_search_conversations,
)
from agentcore.core.errors import AuthenticationError
from agentcore.security import (
    create_access_token,
    create_account_token,
    create_folders_token,
    create_inference_token,
    decode_access_token,
    decode_account_token,
    decode_folders_token,
    decode_inference_token,
)
from agentcore.tools.builtin.read_conversation import ReadConversationTool
from agentcore.tools.builtin.search_conversations import SearchConversationsTool
from agentcore.tools.protocol import ToolContext

pytestmark = pytest.mark.anyio


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="worker",
        backend=SimpleNamespace(location="local"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="host-1",
    )


# --- token mutual exclusion ---------------------------------------------------


def test_account_token_roundtrip():
    token = create_account_token("user-1")
    assert decode_account_token(token) == "user-1"


def test_account_token_rejects_other_types():
    access = create_access_token("user-1", audience="product")
    inference = create_inference_token("user-1")
    folders = create_folders_token("user-1")
    for other in (access, inference, folders):
        with pytest.raises(AuthenticationError):
            decode_account_token(other)


def test_other_decoders_reject_account_token():
    account = create_account_token("user-1")
    with pytest.raises(AuthenticationError):
        decode_access_token(account)
    with pytest.raises(AuthenticationError):
        decode_inference_token(account)
    with pytest.raises(AuthenticationError):
        decode_folders_token(account)


def test_account_token_rejects_expired():
    expired = create_account_token("user-1", expires_delta=timedelta(minutes=-1))
    with pytest.raises(AuthenticationError):
        decode_account_token(expired)


# --- cloud HTTP client --------------------------------------------------------


class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._handler(request)


@pytest.fixture
def account_creds() -> AccountCredentials:
    return AccountCredentials(
        api_key="account-jwt",
        base_url="https://cloud.example/v1/account",
    )


async def test_cloud_search_ok(monkeypatch: pytest.MonkeyPatch, account_creds):
    async def _handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://cloud.example/v1/account/conversations/search"
        assert request.headers["Authorization"] == "Bearer account-jwt"
        body = httpx.Request("POST", str(request.url), content=request.content)
        del body
        return httpx.Response(
            200,
            json={
                "rows": [
                    {
                        "conversation_id": "c1",
                        "title": "Alpha",
                        "folder_id": None,
                        "folder_name": None,
                        "updated_at": "2026-08-09T12:00:00",
                        "message_count": 3,
                        "archived": False,
                        "snippet": None,
                    }
                ],
                "folder_miss": False,
            },
        )

    monkeypatch.setattr(
        "agentcore.account.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    data = await cloud_search_conversations(
        account_creds,
        payload={"query": "", "limit": 10},
    )
    assert len(data["rows"]) == 1
    assert data["rows"][0]["conversation_id"] == "c1"


async def test_cloud_read_ok(monkeypatch: pytest.MonkeyPatch, account_creds):
    async def _handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/conversations/read")
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "title": "T",
                "conversation_id": "c1",
                "transcript": "hello",
                "truncated": False,
                "next_cursor": None,
                "started_at": None,
                "ended_at": None,
                "message_count": 1,
                "char_offset": 0,
                "total_chars": 5,
            },
        )

    monkeypatch.setattr(
        "agentcore.account.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    data = await cloud_read_conversation(
        account_creds,
        payload={"conversation_id": "c1"},
    )
    assert data["status"] == "ok"
    assert data["transcript"] == "hello"


async def test_cloud_chat_context_ok(monkeypatch: pytest.MonkeyPatch, account_creds):
    async def _handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == (
            "https://cloud.example/v1/account/conversations/chat-context"
        )
        assert request.headers["Authorization"] == "Bearer account-jwt"
        return httpx.Response(
            200,
            json={
                "history": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "ok"},
                ]
            },
        )

    monkeypatch.setattr(
        "agentcore.account.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    data = await cloud_chat_context(account_creds, conversation_id="c1")
    assert data["history"][0]["content"] == "hi"


async def test_cloud_search_unauthorized(monkeypatch: pytest.MonkeyPatch, account_creds):
    async def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"detail": "nope"})

    monkeypatch.setattr(
        "agentcore.account.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    with pytest.raises(AccountCloudError) as ei:
        await cloud_search_conversations(account_creds, payload={})
    assert ei.value.code == "account_cloud_unauthorized"


async def test_cloud_search_unreachable(monkeypatch: pytest.MonkeyPatch, account_creds):
    async def _handler(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(
        "agentcore.account.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    with pytest.raises(AccountCloudError) as ei:
        await cloud_search_conversations(account_creds, payload={})
    assert ei.value.code == "account_cloud_unreachable"


# --- tools: cloud vs DB -------------------------------------------------------


async def test_search_uses_cloud_when_creds_bound(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    import agentcore.tools.builtin.search_conversations as search_mod

    db_called = {"n": 0}

    class _CM:
        async def __aenter__(self) -> object:
            db_called["n"] += 1
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(search_mod, "async_session_factory", lambda: _CM())

    async def _fake_search(creds: AccountCredentials, *, payload: dict[str, Any]):
        assert creds.api_key == "account-jwt"
        assert payload["exclude_conversation_id"] == "host-1"
        return {
            "rows": [
                {
                    "conversation_id": "cloud-c",
                    "title": "Via HTTP",
                    "folder_id": None,
                    "folder_name": None,
                    "updated_at": "2026-08-09T12:00:00",
                    "message_count": 2,
                    "archived": False,
                }
            ],
            "folder_miss": False,
        }

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_search_conversations",
        _fake_search,
    )

    with account_credentials_scope(account_creds):
        result = await SearchConversationsTool().execute({}, _ctx())
    assert result.success
    assert "cloud-c" in result.output
    assert db_called["n"] == 0


async def test_search_cloud_failure_is_hard_fail(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    async def _boom(creds: AccountCredentials, *, payload: dict[str, Any]):
        del creds, payload
        raise AccountCloudError("down", code="account_cloud_unreachable")

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_search_conversations",
        _boom,
    )
    with account_credentials_scope(account_creds):
        result = await SearchConversationsTool().execute({}, _ctx())
    assert result.success is False
    assert result.error == "account_cloud_unreachable"
    assert "失败" in result.output


async def test_read_uses_cloud_when_creds_bound(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    import agentcore.tools.builtin.read_conversation as read_mod

    class _CM:
        async def __aenter__(self) -> object:
            raise AssertionError("must not hit DB when account creds bound")

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(read_mod, "async_session_factory", lambda: _CM())

    async def _fake_read(creds: AccountCredentials, *, payload: dict[str, Any]):
        assert payload["conversation_id"] == "past-1"
        return {
            "status": "ok",
            "title": "Past",
            "conversation_id": "past-1",
            "transcript": "body text",
            "truncated": False,
            "next_cursor": None,
            "started_at": None,
            "ended_at": None,
            "message_count": 1,
            "char_offset": 0,
            "total_chars": 9,
        }

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_read_conversation",
        _fake_read,
    )

    with account_credentials_scope(account_creds):
        result = await ReadConversationTool().execute(
            {"conversation_id": "past-1"}, _ctx()
        )
    assert result.success
    assert "body text" in result.output
    assert result.display["conversation_id"] == "past-1"


async def test_read_cloud_soft_miss(monkeypatch: pytest.MonkeyPatch, account_creds):
    async def _fake_read(creds: AccountCredentials, *, payload: dict[str, Any]):
        del creds, payload
        return {"status": "soft_miss", "conversation_id": "missing"}

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_read_conversation",
        _fake_read,
    )
    with account_credentials_scope(account_creds):
        result = await ReadConversationTool().execute(
            {"conversation_id": "missing"}, _ctx()
        )
    assert result.success is True
    assert "无法打开" in result.output


async def test_read_cloud_failure_is_hard_fail(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    async def _boom(creds: AccountCredentials, *, payload: dict[str, Any]):
        del creds, payload
        raise AccountCloudError("down", code="account_cloud_unreachable")

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_read_conversation",
        _boom,
    )
    with account_credentials_scope(account_creds):
        result = await ReadConversationTool().execute(
            {"conversation_id": "past-1"}, _ctx()
        )
    assert result.success is False
    assert result.error == "account_cloud_unreachable"


async def test_mint_account_token_response():
    from agentcore.api.routes.account import mint_account_token

    user = SimpleNamespace(user_id="u1")
    resp = await mint_account_token(user)  # type: ignore[arg-type]
    assert resp.expires_in_sec > 0
    assert decode_account_token(resp.token) == "u1"


async def test_account_api_user_accepts_account_bearer(monkeypatch: pytest.MonkeyPatch):
    from agentcore.api import dependencies as deps

    user = SimpleNamespace(user_id="u1", status="active", role="user")

    class _Repo:
        async def get_by_id(self, user_id: str):
            assert user_id == "u1"
            return user

    request = SimpleNamespace(
        url=SimpleNamespace(path="/v1/account/conversations/search"),
        state=SimpleNamespace(),
    )
    token = create_account_token("u1")
    got = await deps.get_account_api_user(
        request,  # type: ignore[arg-type]
        access_token=None,
        authorization=f"Bearer {token}",
        user_repo=_Repo(),  # type: ignore[arg-type]
    )
    assert got.user_id == "u1"


async def test_account_api_user_rejects_folders_and_inference():
    from agentcore.api import dependencies as deps

    class _Repo:
        async def get_by_id(self, user_id: str):
            raise AssertionError("should not load user")

    request = SimpleNamespace(
        url=SimpleNamespace(path="/v1/account/conversations/search"),
        state=SimpleNamespace(),
    )
    for token in (create_folders_token("u1"), create_inference_token("u1")):
        with pytest.raises(AuthenticationError):
            await deps.get_account_api_user(
                request,  # type: ignore[arg-type]
                access_token=None,
                authorization=f"Bearer {token}",
                user_repo=_Repo(),  # type: ignore[arg-type]
            )


async def test_parse_account_auth_from_sidecar_params():
    from agentcore.sidecar.server_pkg.handlers import HandlerMixin

    creds = HandlerMixin._parse_account_auth(
        {
            "accountAuth": {
                "baseUrl": "https://api.example/v1/account",
                "apiKey": "acct-jwt",
            }
        }
    )
    assert creds is not None
    assert creds.base_url == "https://api.example/v1/account"
    assert creds.api_key == "acct-jwt"
    assert HandlerMixin._parse_account_auth({}) is None
    assert HandlerMixin._parse_account_auth({"accountAuth": {"baseUrl": "", "apiKey": "x"}}) is None
