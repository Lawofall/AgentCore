"""P1-8: repository commit strategy — caller owns multi-step unit-of-work."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentcore.auth import AuthService
from agentcore.config import settings
from agentcore.db.repositories._base import commit_or_flush
from agentcore.security import hash_password

_PW = "password123"


@pytest.fixture(autouse=True)
def _open_registration(monkeypatch):
    monkeypatch.setattr(settings, "registration_open", True)


@pytest.mark.asyncio
async def test_commit_or_flush_commits_when_requested():
    session = AsyncMock()
    await commit_or_flush(session, commit=True)
    session.commit.assert_awaited_once()
    session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_commit_or_flush_flushes_when_deferred():
    session = AsyncMock()
    await commit_or_flush(session, commit=False)
    session.flush.assert_awaited_once()
    session.commit.assert_not_called()


class _TrackingUsers:
    def __init__(self) -> None:
        self.create_kwargs: list[dict] = []
        self._by_username: dict = {}

    async def get_by_username(self, username):
        return self._by_username.get(username)

    async def create(
        self,
        *,
        username,
        display_name=None,
        email=None,
        role="user",
        status="active",
        registration_ip=None,
        commit=True,
    ):
        self.create_kwargs.append(
            {
                "commit": commit,
                "username": username,
                "registration_ip": registration_ip,
            }
        )
        user = SimpleNamespace(
            user_id="u1",
            username=username,
            display_name=display_name or username,
            email=email,
            role=role,
            status=status,
            registration_ip=registration_ip,
        )
        self._by_username[username] = user
        return user


class _TrackingCreds:
    def __init__(self, *, fail: bool = False) -> None:
        self.create_kwargs: list[dict] = []
        self._fail = fail
        self._by_user: dict = {}

    async def create(self, *, user_id, password_hash, commit=True):
        self.create_kwargs.append({"commit": commit, "user_id": user_id})
        if self._fail:
            raise RuntimeError("credentials insert failed")
        cred = SimpleNamespace(user_id=user_id, password_hash=password_hash)
        self._by_user[user_id] = cred
        return cred


class _TrackingSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_register_defers_commit_then_commits_once():
    users = _TrackingUsers()
    creds = _TrackingCreds()
    session = _TrackingSession()
    svc = AuthService(
        users=users,  # type: ignore[arg-type]
        credentials=creds,  # type: ignore[arg-type]
        refresh_tokens=SimpleNamespace(),  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
    )
    user = await svc.register(username="atomic", password=_PW)
    assert user.username == "atomic"
    assert users.create_kwargs == [
        {"commit": False, "username": "atomic", "registration_ip": None}
    ]
    assert creds.create_kwargs == [{"commit": False, "user_id": "u1"}]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_register_skips_final_commit_when_credentials_fail():
    users = _TrackingUsers()
    creds = _TrackingCreds(fail=True)
    session = _TrackingSession()
    svc = AuthService(
        users=users,  # type: ignore[arg-type]
        credentials=creds,  # type: ignore[arg-type]
        refresh_tokens=SimpleNamespace(),  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="credentials insert failed"):
        await svc.register(username="orphan", password=_PW)
    assert users.create_kwargs[0]["commit"] is False
    assert session.commits == 0


@pytest.mark.asyncio
async def test_change_password_single_commit():
    from tests.test_auth_service import FakeCredentials, FakeRefreshTokens, FakeUsers

    users = FakeUsers()
    creds = FakeCredentials()
    tokens = FakeRefreshTokens()
    session = _TrackingSession()
    user = await users.create(username="chg", display_name="C")
    await creds.create(user_id=user.user_id, password_hash=hash_password(_PW))
    await tokens.create(
        user_id=user.user_id,
        token_hash="old",
        token_family="fam",
        expires_at=datetime.now(UTC),
    )
    svc = AuthService(
        users=users,
        credentials=creds,
        refresh_tokens=tokens,
        session=session,  # type: ignore[arg-type]
    )
    pair = await svc.change_password(
        user_id=user.user_id,
        current_password=_PW,
        new_password="password456",
        audience="product",
    )
    assert pair.access_token
    assert session.commits == 1
