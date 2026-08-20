"""Unit tests for AuthService using in-memory fake repositories (no DB)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agentcore.auth import AuthService
from agentcore.config import settings
from agentcore.core.errors import (
    AuthenticationError,
    AuthorizationError,
    EmailNotVerifiedError,
    NotFoundError,
    ValidationError,
)
from agentcore.core.types import new_id
from agentcore.security import (
    decode_access_token,
    decode_access_token_claims,
    decode_access_token_mfa_verified,
    hash_refresh_token,
)

_PW = "password123"


@pytest.fixture(autouse=True)
def _open_registration(monkeypatch):
    """Unit tests assume open registration; local .env may close the gate."""
    monkeypatch.setattr(settings, "registration_open", True)
    monkeypatch.setattr(settings, "require_email_verified", False)


async def _do_login(svc: AuthService, **kwargs):
    result = await svc.login(**kwargs)
    assert result.tokens is not None, "expected tokens from login"
    return result.user, result.tokens


class FakeUsers:
    def __init__(self) -> None:
        self._by_id: dict = {}

    async def get_by_id(self, user_id):
        return self._by_id.get(user_id)

    async def get_by_username(self, username):
        lowered = username.strip().lower()
        return next(
            (u for u in self._by_id.values() if u.username.lower() == lowered),
            None,
        )

    async def get_by_email(self, email):
        target = email.strip().lower()
        return next(
            (u for u in self._by_id.values() if u.email and u.email.lower() == target),
            None,
        )

    async def create(
        self,
        *,
        username,
        display_name=None,
        email=None,
        email_verified_at=None,
        role="user",
        status="active",
        registration_ip=None,
        commit=True,
    ):
        user = SimpleNamespace(
            user_id=new_id(),
            username=username,
            display_name=display_name or "",
            email=email,
            email_verified_at=email_verified_at,
            role=role,
            status=status,
            registration_ip=registration_ip,
            deleted_at=None,
            username_changed_at=None,
        )
        self._by_id[user.user_id] = user
        return user

    async def update(self, user_id, **fields):
        # Mirrors the real repo: the service only forwards the keys that changed.
        fields.pop("commit", None)
        user = self._by_id.get(user_id)
        if user is None:
            return None
        for key, value in fields.items():
            setattr(user, key, value)
        return user

    async def soft_delete(self, user_id, *, commit=True):
        user = self._by_id.get(user_id)
        if user is None:
            return None
        user.deleted_at = datetime.now(UTC)
        user.status = "disabled"
        user.username = f"deleted_{user_id}"
        user.email = None
        return user


class FakeCredentials:
    def __init__(self) -> None:
        self._by_user: dict = {}

    async def create(self, *, user_id, password_hash, commit=True):
        cred = SimpleNamespace(
            user_id=user_id,
            password_hash=password_hash,
            failed_attempts=0,
            locked_until=None,
            password_must_change=False,
        )
        self._by_user[user_id] = cred
        return cred

    async def get_by_user_id(self, user_id):
        return self._by_user.get(user_id)

    async def increment_failure(self, user_id, *, max_attempts, lock_until):
        # Mirror SQL ``failed_attempts = failed_attempts + 1`` (no absolute overwrite).
        cred = self._by_user[user_id]
        cred.failed_attempts += 1
        cred.locked_until = lock_until if cred.failed_attempts >= max_attempts else None
        return cred.failed_attempts

    async def reset_failure_state(self, user_id):
        cred = self._by_user[user_id]
        cred.failed_attempts = 0
        cred.locked_until = None

    async def set_password(self, user_id, password_hash, *, must_change=None, commit=True):
        cred = self._by_user[user_id]
        cred.password_hash = password_hash
        cred.failed_attempts = 0
        cred.locked_until = None
        if must_change is not None:
            cred.password_must_change = must_change


class FakeRefreshTokens:
    def __init__(self) -> None:
        self.records: dict = {}

    async def create(
        self,
        *,
        user_id,
        token_hash,
        token_family,
        expires_at,
        client_aud="product",
        client_platform=None,
        user_agent=None,
        ip=None,
        family_started_at=None,
        last_used_at=None,
        persist_session=True,
        commit=True,
    ):
        now = datetime.now(UTC)
        rec = SimpleNamespace(
            id=new_id(),
            user_id=user_id,
            token_hash=token_hash,
            token_family=token_family,
            expires_at=expires_at,
            revoked_at=None,
            rotated_at=None,
            client_aud=client_aud,
            client_platform=client_platform,
            user_agent=user_agent,
            ip=ip,
            family_started_at=family_started_at or now,
            last_used_at=last_used_at or now,
            persist_session=persist_session,
            created_at=now,
        )
        self.records[rec.id] = rec
        return rec

    async def get_by_hash(self, token_hash):
        return next((r for r in self.records.values() if r.token_hash == token_hash), None)

    async def mark_rotated(self, token_id, *, commit=True):
        self.records[token_id].rotated_at = datetime.now(UTC)

    async def revoke_family(self, token_family):
        for rec in self.records.values():
            if rec.token_family == token_family and rec.revoked_at is None:
                rec.revoked_at = datetime.now(UTC)

    async def revoke_all_for_user(self, user_id, *, commit=True):
        for rec in self.records.values():
            if rec.user_id == user_id and rec.revoked_at is None:
                rec.revoked_at = datetime.now(UTC)

    async def revoke_other_families(self, user_id, *, keep_family):
        n = 0
        for rec in self.records.values():
            if (
                rec.user_id == user_id
                and rec.token_family != keep_family
                and rec.revoked_at is None
            ):
                rec.revoked_at = datetime.now(UTC)
                n += 1
        return n

    async def family_belongs_to_user(self, *, user_id, token_family):
        return any(
            r.user_id == user_id and r.token_family == token_family
            for r in self.records.values()
        )

    async def list_active_session_tips(self, *, user_id, now=None):
        now = now or datetime.now(UTC)
        return [
            r
            for r in self.records.values()
            if r.user_id == user_id
            and r.revoked_at is None
            and r.rotated_at is None
            and r.expires_at > now
        ]

    async def delete_terminal_stale(self, *, before, limit):
        now = datetime.now(UTC)
        doomed = []
        for rec in self.records.values():
            terminal = rec.revoked_at or rec.rotated_at
            if terminal is None and rec.expires_at < now:
                terminal = rec.expires_at
            if terminal is not None and terminal < before:
                doomed.append(rec.id)
            if len(doomed) >= limit:
                break
        for rid in doomed:
            del self.records[rid]
        return len(doomed)




def _make():
    users = FakeUsers()
    creds = FakeCredentials()
    tokens = FakeRefreshTokens()
    svc = AuthService(users=users, credentials=creds, refresh_tokens=tokens)
    return svc, users, creds, tokens


# --- register ---


async def test_register_success():
    svc, _users, creds, _tokens = _make()
    user = await svc.register(username="alice", password=_PW)
    assert user.username == "alice"
    cred = await creds.get_by_user_id(user.user_id)
    assert cred is not None and cred.password_hash != _PW


async def test_register_writes_registration_ip():
    svc, _users, _creds, _tokens = _make()
    user = await svc.register(
        username="ipuser", password=_PW, registration_ip="203.0.113.10"
    )
    assert user.registration_ip == "203.0.113.10"


async def test_register_closed_raises_authorization_error(monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "registration_open", False)
    svc, *_ = _make()
    with pytest.raises(AuthorizationError, match="注册已关闭"):
        await svc.register(username="bob", password=_PW)


async def test_register_rejects_duplicate_username():
    svc, *_ = _make()
    await svc.register(username="dave", password=_PW)
    with pytest.raises(ValidationError):
        await svc.register(username="dave", password=_PW)


async def test_register_rejects_weak_password():
    svc, *_ = _make()
    with pytest.raises(ValidationError):
        await svc.register(username="eve", password="short")


async def test_register_rejects_at_in_username():
    svc, *_ = _make()
    with pytest.raises(ValidationError, match="@"):
        await svc.register(username="ada@example.com", password=_PW)


# --- login ---


async def test_login_success_issues_tokens():
    svc, _u, _c, tokens = _make()
    user = await svc.register(username="frank", password=_PW)
    logged_user, pair = await _do_login(svc, username="frank", password=_PW)
    assert logged_user.user_id == user.user_id
    assert decode_access_token(pair.access_token) == user.user_id
    assert pair.refresh_token
    assert len(tokens.records) == 1


async def test_login_by_email_success():
    svc, *_ = _make()
    user = await svc.register(
        username="frankmail", password=_PW, email="Frank@Example.com"
    )
    logged, _pair = await _do_login(svc, username="frank@example.com", password=_PW)
    assert logged.user_id == user.user_id
    again, _pair = await _do_login(svc, username="frankmail", password=_PW)
    assert again.user_id == user.user_id


async def test_login_empty_email_account_still_uses_username():
    svc, *_ = _make()
    user = await svc.register(username="legacy", password=_PW)
    assert user.email is None
    logged, _pair = await _do_login(svc, username="legacy", password=_PW)
    assert logged.user_id == user.user_id
    with pytest.raises(AuthenticationError, match="用户名或密码"):
        await svc.login(username="legacy@example.com", password=_PW)


async def test_login_unknown_email_matches_unknown_username():
    svc, *_ = _make()
    with pytest.raises(AuthenticationError, match="用户名或密码"):
        await svc.login(username="ghost@example.com", password=_PW)


async def test_login_malformed_email_is_auth_error():
    svc, *_ = _make()
    with pytest.raises(AuthenticationError, match="用户名或密码"):
        await svc.login(username="not-an-email@", password=_PW)


async def test_login_wrong_password_raises_and_counts():
    svc, _u, creds, _t = _make()
    user = await svc.register(username="grace", password=_PW)
    with pytest.raises(AuthenticationError):
        await svc.login(username="grace", password="wrong-pw")
    cred = await creds.get_by_user_id(user.user_id)
    assert cred.failed_attempts == 1


async def test_login_failure_increments_are_additive():
    """Concurrent wrong-password paths must not lose counts via absolute overwrite.

    Two handlers that each saw ``failed_attempts=0`` would both write ``1`` under
    read-modify-write; atomic ``+= 1`` yields ``2``. FakeCredentials mirrors the
    SQL increment contract.
    """
    svc, _u, creds, _t = _make()
    user = await svc.register(username="racey", password=_PW)
    now = datetime.now(UTC)
    await svc._register_failure(user.user_id, now)
    await svc._register_failure(user.user_id, now)
    cred = await creds.get_by_user_id(user.user_id)
    assert cred.failed_attempts == 2
    assert cred.locked_until is None


async def test_login_locks_after_max_attempts():
    svc, _u, creds, _t = _make()
    user = await svc.register(username="heidi", password=_PW)
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            await svc.login(username="heidi", password="wrong-pw")
    cred = await creds.get_by_user_id(user.user_id)
    assert cred.locked_until is not None
    # correct password is still rejected while locked
    with pytest.raises(AuthenticationError):
        await svc.login(username="heidi", password=_PW)


async def test_login_unknown_user_raises():
    svc, *_ = _make()
    with pytest.raises(AuthenticationError):
        await svc.login(username="ghost", password=_PW)


async def test_login_unknown_user_still_runs_verify_for_constant_time(monkeypatch):
    """SEC-004: a missing username must still run one password verify (against the dummy
    hash) so its timing matches the wrong-password path — no enumeration oracle."""
    import agentcore.auth.service as service_mod

    calls: list[tuple[str, str]] = []
    real_verify = service_mod.verify_password

    def _spy(password: str, password_hash: str) -> bool:
        calls.append((password, password_hash))
        return real_verify(password, password_hash)

    monkeypatch.setattr(service_mod, "verify_password", _spy)
    svc, *_ = _make()
    with pytest.raises(AuthenticationError):
        await svc.login(username="ghost", password=_PW)
    # verify ran exactly once, against the dummy hash (the branch is not short-circuited).
    assert len(calls) == 1
    assert calls[0][1] == service_mod._DUMMY_PASSWORD_HASH


async def test_login_resets_failures_on_success():
    svc, _u, creds, _t = _make()
    user = await svc.register(username="ivan", password=_PW)
    with pytest.raises(AuthenticationError):
        await svc.login(username="ivan", password="wrong-pw")
    await _do_login(svc,username="ivan", password=_PW)
    cred = await creds.get_by_user_id(user.user_id)
    assert cred.failed_attempts == 0 and cred.locked_until is None


async def test_login_unverified_allowed_by_default():
    svc, users, _c, _t = _make()
    user = await svc.register(username="unverified", password=_PW)
    assert user.email_verified_at is None
    logged, _pair = await _do_login(svc, username="unverified", password=_PW)
    assert logged.user_id == user.user_id


async def test_login_unverified_blocked_when_required(monkeypatch):
    monkeypatch.setattr(settings, "require_email_verified", True)
    svc, _u, _c, _t = _make()
    await svc.register(username="gated", password=_PW)
    with pytest.raises(EmailNotVerifiedError):
        await svc.login(username="gated", password=_PW)


async def test_login_verified_passes_when_required(monkeypatch):
    monkeypatch.setattr(settings, "require_email_verified", True)
    svc, users, _c, _t = _make()
    user = await svc.register(username="proved", password=_PW)
    users._by_id[user.user_id].email_verified_at = datetime.now(UTC)
    logged, _pair = await _do_login(svc, username="proved", password=_PW)
    assert logged.user_id == user.user_id


# --- refresh / logout ---


async def test_refresh_rotates_token():
    svc, _u, _c, tokens = _make()
    await svc.register(username="judy", password=_PW)
    _, pair = await _do_login(svc,username="judy", password=_PW)
    new_pair = await svc.refresh(refresh_token=pair.refresh_token)
    assert new_pair.refresh_token != pair.refresh_token
    assert len(tokens.records) == 2
    rotated = [r for r in tokens.records.values() if r.rotated_at is not None]
    assert len(rotated) == 1


async def test_refresh_reuse_beyond_grace_revokes_family():
    svc, _u, _c, tokens = _make()
    await svc.register(username="ken", password=_PW)
    _, pair = await _do_login(svc,username="ken", password=_PW)
    await svc.refresh(refresh_token=pair.refresh_token)  # rotate once
    # Age the rotation past the grace window so re-presenting the old token reads
    # as a genuine leak/replay (not benign concurrency) -> family revoked.
    rec = await tokens.get_by_hash(hash_refresh_token(pair.refresh_token))
    rec.rotated_at = datetime.now(UTC) - timedelta(minutes=1)
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=pair.refresh_token)  # reuse old token
    assert all(r.revoked_at is not None for r in tokens.records.values())


async def test_refresh_reuse_within_grace_is_benign():
    # The dominant cause of spurious mid-session logout: several requests 401 at
    # once on an expired access token and refresh with the *same* cookie. Within
    # the grace window that must NOT revoke the family — it mints a fresh successor
    # and everyone stays logged in (认证与会话.md §五).
    svc, _u, _c, tokens = _make()
    await svc.register(username="kim", password=_PW)
    _, pair = await _do_login(svc,username="kim", password=_PW)
    first = await svc.refresh(refresh_token=pair.refresh_token)  # rotate once
    second = await svc.refresh(refresh_token=pair.refresh_token)  # racing replay
    assert second.refresh_token and second.refresh_token != first.refresh_token
    # Nobody is revoked: the session survives the concurrent refresh.
    assert all(r.revoked_at is None for r in tokens.records.values())
    # Both freshly minted successors remain usable going forward.
    assert (await svc.refresh(refresh_token=first.refresh_token)).refresh_token
    assert (await svc.refresh(refresh_token=second.refresh_token)).refresh_token


async def test_refresh_unknown_token_raises():
    svc, *_ = _make()
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token="does-not-exist")


async def test_refresh_expired_token_raises():
    svc, _u, _c, tokens = _make()
    await svc.register(username="leo", password=_PW)
    _, pair = await _do_login(svc,username="leo", password=_PW)
    rec = await tokens.get_by_hash(hash_refresh_token(pair.refresh_token))
    rec.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=pair.refresh_token)


async def test_logout_revokes_family():
    svc, _u, _c, tokens = _make()
    await svc.register(username="mia", password=_PW)
    _, pair = await _do_login(svc,username="mia", password=_PW)
    await svc.logout(refresh_token=pair.refresh_token)
    assert all(r.revoked_at is not None for r in tokens.records.values())
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=pair.refresh_token)



# --- admin password reset (重置密码) ---


async def test_admin_reset_password_rotates_secret_and_revokes_sessions():
    svc, _u, creds, tokens = _make()
    user = await svc.register(username="nora", password=_PW)
    _, pair = await _do_login(svc,username="nora", password=_PW)

    temp = await svc.admin_reset_password(user_id=user.user_id)
    assert len(temp) >= 8 and temp != _PW
    cred = await creds.get_by_user_id(user.user_id)
    assert cred.password_must_change is True

    # old password no longer works; the freshly minted one does
    with pytest.raises(AuthenticationError):
        await svc.login(username="nora", password=_PW)
    relogged, _ = await _do_login(svc,username="nora", password=temp)
    assert relogged.user_id == user.user_id

    # every pre-reset session is revoked (the old refresh token is dead)
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=pair.refresh_token)


async def test_admin_reset_password_clears_lockout():
    svc, _u, creds, _t = _make()
    user = await svc.register(username="omar", password=_PW)
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            await svc.login(username="omar", password="wrong-pw")
    assert (await creds.get_by_user_id(user.user_id)).locked_until is not None

    temp = await svc.admin_reset_password(user_id=user.user_id)
    cred = await creds.get_by_user_id(user.user_id)
    assert cred.locked_until is None and cred.failed_attempts == 0
    relogged, _ = await _do_login(svc,username="omar", password=temp)
    assert relogged.user_id == user.user_id


async def test_admin_reset_password_unknown_user_raises_not_found():
    svc, *_ = _make()
    with pytest.raises(NotFoundError):
        await svc.admin_reset_password(user_id="ghost")


# --- admin set password (设置密码) ---


async def test_admin_set_password_rotates_secret_and_revokes_sessions():
    svc, _u, creds, tokens = _make()
    user = await svc.register(username="setme", password=_PW)
    _, pair = await _do_login(svc,username="setme", password=_PW)

    custom = "custompass99"
    await svc.admin_set_password(user_id=user.user_id, new_password=custom)
    cred = await creds.get_by_user_id(user.user_id)
    assert cred.password_must_change is True

    with pytest.raises(AuthenticationError):
        await svc.login(username="setme", password=_PW)
    relogged, _ = await _do_login(svc,username="setme", password=custom)
    assert relogged.user_id == user.user_id

    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=pair.refresh_token)


async def test_admin_set_password_force_change_false():
    svc, _u, creds, _tokens = _make()
    user = await svc.register(username="perm", password=_PW)

    await svc.admin_set_password(
        user_id=user.user_id, new_password="permanent1", force_change=False
    )
    cred = await creds.get_by_user_id(user.user_id)
    assert cred.password_must_change is False


async def test_admin_set_password_weak_raises():
    svc, _u, _creds, _tokens = _make()
    user = await svc.register(username="weak", password=_PW)
    with pytest.raises(ValidationError):
        await svc.admin_set_password(user_id=user.user_id, new_password="short")


async def test_admin_set_password_unknown_user_raises_not_found():
    svc, *_ = _make()
    with pytest.raises(NotFoundError):
        await svc.admin_set_password(user_id="ghost", new_password="longenough")


# --- change password (self-service 修改密码) ---


async def test_change_password_rotates_secret_and_keeps_current_session():
    svc, _u, _c, _t = _make()
    user = await svc.register(username="pia", password=_PW)
    _, old_pair = await _do_login(svc,username="pia", password=_PW)

    new_pair = await svc.change_password(
        user_id=user.user_id,
        current_password=_PW,
        new_password="brand-new-pw",
        audience="product",
    )

    # old password dead, new one works
    with pytest.raises(AuthenticationError):
        await svc.login(username="pia", password=_PW)
    relogged, _ = await _do_login(svc,username="pia", password="brand-new-pw")
    assert relogged.user_id == user.user_id

    # the returned pair is a live session; the pre-change one was revoked
    rotated = await svc.refresh(refresh_token=new_pair.refresh_token)
    assert rotated.refresh_token
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=old_pair.refresh_token)


async def test_change_password_clears_must_change_flag():
    svc, _u, creds, _t = _make()
    user = await svc.register(username="sam", password=_PW)
    temp = await svc.admin_reset_password(user_id=user.user_id)
    assert (await creds.get_by_user_id(user.user_id)).password_must_change is True

    await svc.change_password(
        user_id=user.user_id,
        current_password=temp,
        new_password="brand-new-pw",
        audience="product",
    )
    assert (await creds.get_by_user_id(user.user_id)).password_must_change is False


async def test_change_password_wrong_current_raises():
    svc, _u, _c, _t = _make()
    user = await svc.register(username="quinn", password=_PW)
    with pytest.raises(AuthenticationError):
        await svc.change_password(
            user_id=user.user_id,
            current_password="nope",
            new_password="brand-new-pw",
            audience="product",
        )


async def test_change_password_weak_new_raises():
    svc, _u, _c, _t = _make()
    user = await svc.register(username="rob", password=_PW)
    with pytest.raises(ValidationError):
        await svc.change_password(
            user_id=user.user_id,
            current_password=_PW,
            new_password="short",
            audience="product",
        )


async def test_change_password_same_as_current_raises():
    svc, _u, _c, _t = _make()
    user = await svc.register(username="sue", password=_PW)
    with pytest.raises(ValidationError):
        await svc.change_password(
            user_id=user.user_id,
            current_password=_PW,
            new_password=_PW,
            audience="product",
        )


async def test_change_password_keeps_admin_audience_and_mfa_claim():
    """An admin changing their password must stay an *admin* session.

    A ``product`` successor still passes ``/v1/auth/me`` (auth paths are exempt from
    the audience gate), so the console looks logged in while every ``/v1/admin/*``
    call 403s — and refreshing cannot recover it, because the stored family carries
    the wrong ``client_aud`` too.
    """
    svc, _u, _c, tokens = _make()
    user = await svc.register(username="root-admin", password=_PW)

    pair = await svc.change_password(
        user_id=user.user_id,
        current_password=_PW,
        new_password="brand-new-pw",
        audience="admin",
        mfa_verified=True,
    )

    assert decode_access_token_claims(pair.access_token) == (user.user_id, "admin")
    assert decode_access_token_mfa_verified(pair.access_token) is True
    live = [r for r in tokens.records.values() if r.revoked_at is None]
    assert [r.client_aud for r in live] == ["admin"]


async def test_change_password_admin_audience_survives_refresh():
    """The successor family must keep minting admin tokens, not just the first pair."""
    svc, _u, _c, _t = _make()
    user = await svc.register(username="root-admin-2", password=_PW)

    pair = await svc.change_password(
        user_id=user.user_id,
        current_password=_PW,
        new_password="brand-new-pw",
        audience="admin",
        mfa_verified=True,
    )
    rotated = await svc.refresh(refresh_token=pair.refresh_token)

    assert decode_access_token_claims(rotated.access_token) == (user.user_id, "admin")


async def test_change_password_keeps_product_audience_for_desktop():
    """The desktop half of the same endpoint must not be dragged to ``admin``."""
    svc, _u, _c, tokens = _make()
    user = await svc.register(username="desktop-user", password=_PW)

    pair = await svc.change_password(
        user_id=user.user_id,
        current_password=_PW,
        new_password="brand-new-pw",
        audience="product",
    )

    assert decode_access_token_claims(pair.access_token) == (user.user_id, "product")
    assert decode_access_token_mfa_verified(pair.access_token) is False
    live = [r for r in tokens.records.values() if r.revoked_at is None]
    assert [r.client_aud for r in live] == ["product"]


# --- update profile (个人资料编辑) ---


async def test_update_profile_changes_display_name():
    svc, _u, _c, _t = _make()
    user = await svc.register(username="tom", password=_PW)
    updated = await svc.update_profile(user_id=user.user_id, display_name="Tommy")
    assert updated.display_name == "Tommy"


async def test_update_profile_sets_and_clears_email():
    svc, _u, _c, _t = _make()
    user = await svc.register(username="ula", password=_PW)
    updated = await svc.update_profile(user_id=user.user_id, email="ula@example.com")
    assert updated.email == "ula@example.com"
    cleared = await svc.update_profile(user_id=user.user_id, email=None)
    assert cleared.email is None
    assert cleared.email_verified_at is None


async def test_update_profile_email_change_clears_verified_at():
    svc, users, _c, _t = _make()
    user = await svc.register(username="vera", password=_PW, email="vera@example.com")
    users._by_id[user.user_id].email_verified_at = datetime.now(UTC)
    updated = await svc.update_profile(user_id=user.user_id, email="new@example.com")
    assert updated.email == "new@example.com"
    assert updated.email_verified_at is None


async def test_update_profile_rejects_duplicate_email():
    svc, _u, _c, _t = _make()
    first = await svc.register(username="vic", password=_PW)
    second = await svc.register(username="wes", password=_PW)
    await svc.update_profile(user_id=first.user_id, email="taken@example.com")
    with pytest.raises(ValidationError):
        await svc.update_profile(user_id=second.user_id, email="taken@example.com")


async def test_update_profile_rejects_empty_display_name():
    svc, _u, _c, _t = _make()
    user = await svc.register(username="xena", password=_PW)
    with pytest.raises(ValidationError):
        await svc.update_profile(user_id=user.user_id, display_name="   ")


async def test_update_profile_partial_leaves_other_fields():
    svc, _u, _c, _t = _make()
    user = await svc.register(
        username="yan", password=_PW, email="yan@example.com"
    )
    updated = await svc.update_profile(user_id=user.user_id, display_name="Yan!")
    assert updated.display_name == "Yan!" and updated.email == "yan@example.com"


async def test_update_profile_unknown_user_raises_not_found():
    svc, *_ = _make()
    with pytest.raises(NotFoundError):
        await svc.update_profile(user_id="ghost", display_name="Nobody")


# --- delete account (注销账户: 软删 + 匿名化) ---


async def test_delete_account_soft_deletes_anonymizes_and_revokes():
    svc, users, _c, _t = _make()
    user = await svc.register(username="zoe", password=_PW)
    _, pair = await _do_login(svc,username="zoe", password=_PW)

    await svc.delete_account(user_id=user.user_id, password=_PW)

    row = await users.get_by_id(user.user_id)
    assert row.deleted_at is not None
    assert row.status == "disabled"
    assert row.username == f"deleted_{user.user_id}"
    assert row.email is None

    # the old username/session no longer authenticate
    with pytest.raises(AuthenticationError):
        await svc.login(username="zoe", password=_PW)
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=pair.refresh_token)


async def test_delete_account_wrong_password_raises_and_keeps_account():
    svc, users, _c, _t = _make()
    user = await svc.register(username="abe", password=_PW)
    with pytest.raises(AuthenticationError):
        await svc.delete_account(user_id=user.user_id, password="wrong-pw")
    row = await users.get_by_id(user.user_id)
    assert row.deleted_at is None and row.status == "active"


async def test_delete_account_unknown_user_raises_not_found():
    svc, *_ = _make()
    with pytest.raises(NotFoundError):
        await svc.delete_account(user_id="ghost", password=_PW)


# --- sessions + family absolute max ---


async def test_access_token_carries_fam_claim():
    from agentcore.security import decode_access_token_family

    svc, *_ = _make()
    await svc.register(username="fam1", password=_PW)
    _, pair = await _do_login(svc, username="fam1", password=_PW)
    fam = decode_access_token_family(pair.access_token)
    assert fam
    tips = await svc._refresh_tokens.list_active_session_tips(
        user_id=(await svc._users.get_by_username("fam1")).user_id
    )
    assert any(t.token_family == fam for t in tips)


async def test_list_sessions_marks_current_and_aggregates():
    svc, *_ = _make()
    await svc.register(username="sess1", password=_PW)
    user, pair_a = await _do_login(svc, username="sess1", password=_PW)
    _, pair_b = await _do_login(svc, username="sess1", password=_PW)
    from agentcore.security import decode_access_token_family

    fam_b = decode_access_token_family(pair_b.access_token)
    sessions = await svc.list_sessions(user_id=user.user_id, current_family=fam_b)
    assert len(sessions) == 2
    currents = [s for s in sessions if s.current]
    assert len(currents) == 1 and currents[0].id == fam_b


async def test_revoke_session_kills_refresh():
    svc, *_ = _make()
    await svc.register(username="sess2", password=_PW)
    user, pair = await _do_login(svc, username="sess2", password=_PW)
    from agentcore.security import decode_access_token_family

    fam = decode_access_token_family(pair.access_token)
    assert fam
    await svc.revoke_session(user_id=user.user_id, family_id=fam)
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=pair.refresh_token)


async def test_revoke_session_foreign_family_404():
    svc, *_ = _make()
    await svc.register(username="own", password=_PW)
    await svc.register(username="oth", password=_PW)
    owner, _ = await _do_login(svc, username="own", password=_PW)
    _, other_pair = await _do_login(svc, username="oth", password=_PW)
    from agentcore.security import decode_access_token_family

    other_fam = decode_access_token_family(other_pair.access_token)
    with pytest.raises(NotFoundError):
        await svc.revoke_session(user_id=owner.user_id, family_id=other_fam)


async def test_revoke_other_sessions_keeps_current():
    svc, *_ = _make()
    await svc.register(username="sess3", password=_PW)
    user, pair_a = await _do_login(svc, username="sess3", password=_PW)
    _, pair_b = await _do_login(svc, username="sess3", password=_PW)
    from agentcore.security import decode_access_token_family

    fam_b = decode_access_token_family(pair_b.access_token)
    await svc.revoke_other_sessions(user_id=user.user_id, current_family=fam_b)
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=pair_a.refresh_token)
    rotated = await svc.refresh(refresh_token=pair_b.refresh_token)
    assert rotated.refresh_token


async def test_family_absolute_max_rejects_product(monkeypatch):
    monkeypatch.setattr(
        "agentcore.auth.service.settings.refresh_family_max_days", 1
    )
    svc, *_ = _make()
    await svc.register(username="max1", password=_PW)
    _, pair = await _do_login(svc, username="max1", password=_PW)
    tip = next(iter(svc._refresh_tokens.records.values()))
    tip.family_started_at = datetime.now(UTC) - timedelta(days=2)
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=pair.refresh_token)


async def test_family_absolute_max_admin_hours(monkeypatch):
    monkeypatch.setattr(
        "agentcore.auth.service.settings.admin_refresh_family_max_hours", 1
    )
    monkeypatch.setattr(
        "agentcore.auth.service.settings.admin_mfa_required", False
    )
    svc, users, creds, tokens = _make()
    admin = await users.create(username="admmax", display_name="A", role="admin")
    from agentcore.security import hash_password

    await creds.create(user_id=admin.user_id, password_hash=hash_password(_PW))
    _, pair = await _do_login(
        svc, username="admmax", password=_PW, platform="admin"
    )
    tip = next(iter(tokens.records.values()))
    tip.family_started_at = datetime.now(UTC) - timedelta(hours=2)
    assert tip.client_aud == "admin"
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=pair.refresh_token)


async def test_persist_session_false_short_refresh_ttl(monkeypatch):
    monkeypatch.setattr(
        "agentcore.auth.service.settings.ephemeral_refresh_family_max_hours", 8
    )
    monkeypatch.setattr(
        "agentcore.auth.service.settings.jwt_refresh_token_expire_days", 30
    )
    svc, *_ = _make()
    await svc.register(username="ephem1", password=_PW)
    _, pair = await _do_login(
        svc, username="ephem1", password=_PW, persist_session=False
    )
    assert pair.persist_session is False
    tip = next(iter(svc._refresh_tokens.records.values()))
    assert tip.persist_session is False
    remaining = tip.expires_at - tip.family_started_at
    assert remaining <= timedelta(hours=8) + timedelta(seconds=2)
    assert remaining < timedelta(days=1)


async def test_persist_session_true_long_refresh_ttl():
    svc, *_ = _make()
    await svc.register(username="persist1", password=_PW)
    _, pair = await _do_login(svc, username="persist1", password=_PW)
    assert pair.persist_session is True
    tip = next(iter(svc._refresh_tokens.records.values()))
    assert tip.persist_session is True
    remaining = tip.expires_at - datetime.now(UTC)
    assert remaining > timedelta(days=20)


async def test_ephemeral_family_absolute_max_rejects(monkeypatch):
    monkeypatch.setattr(
        "agentcore.auth.service.settings.ephemeral_refresh_family_max_hours", 1
    )
    svc, *_ = _make()
    await svc.register(username="ephem2", password=_PW)
    _, pair = await _do_login(
        svc, username="ephem2", password=_PW, persist_session=False
    )
    tip = next(iter(svc._refresh_tokens.records.values()))
    tip.family_started_at = datetime.now(UTC) - timedelta(hours=2)
    with pytest.raises(AuthenticationError):
        await svc.refresh(refresh_token=pair.refresh_token)


async def test_ephemeral_refresh_inherits_persist_flag():
    svc, *_ = _make()
    await svc.register(username="ephem3", password=_PW)
    _, pair = await _do_login(
        svc, username="ephem3", password=_PW, persist_session=False
    )
    rotated = await svc.refresh(refresh_token=pair.refresh_token)
    assert rotated.persist_session is False
    tips = [
        r
        for r in svc._refresh_tokens.records.values()
        if r.rotated_at is None and r.revoked_at is None
    ]
    assert tips and tips[0].persist_session is False


async def test_gc_deletes_only_terminal_old_rows():
    svc, *_ = _make()
    await svc.register(username="gc1", password=_PW)
    user, pair = await _do_login(svc, username="gc1", password=_PW)
    # Rotate once → old tip is terminal (rotated).
    await svc.refresh(refresh_token=pair.refresh_token)
    tokens = svc._refresh_tokens
    old = [r for r in tokens.records.values() if r.rotated_at is not None][0]
    live = [r for r in tokens.records.values() if r.rotated_at is None][0]
    old.rotated_at = datetime.now(UTC) - timedelta(days=10)
    deleted = await tokens.delete_terminal_stale(
        before=datetime.now(UTC) - timedelta(days=7), limit=100
    )
    assert deleted == 1
    assert old.id not in tokens.records
    assert live.id in tokens.records


class FakeMfa:
    def __init__(self, *, enrolled: bool = True) -> None:
        self.enrolled = enrolled

    async def is_enrolled(self, user_id: str) -> bool:
        return self.enrolled

    async def verify_code(self, *, user_id: str, code: str) -> bool:
        return code == "123456"

    async def verify_recovery_code(self, *, user_id: str, code: str) -> bool:
        return False


def _make_admin_with_mfa(*, enrolled: bool = True):
    users = FakeUsers()
    creds = FakeCredentials()
    tokens = FakeRefreshTokens()
    mfa = FakeMfa(enrolled=enrolled)
    svc = AuthService(
        users=users,
        credentials=creds,
        refresh_tokens=tokens,
        mfa=mfa,
    )
    return svc, users, creds, tokens, mfa


async def test_complete_mfa_login_sets_mfa_claim_on_access_token():
    from agentcore.security import (
        create_mfa_pending_token,
        decode_access_token_mfa_verified,
        hash_password,
    )

    svc, users, creds, *_ = _make_admin_with_mfa(enrolled=True)
    admin = await users.create(username="mfaadm", display_name="A", role="admin")
    await creds.create(user_id=admin.user_id, password_hash=hash_password(_PW))
    pending = create_mfa_pending_token(admin.user_id, audience="admin")
    user, pair = await svc.complete_mfa_login(pending_token=pending, code="123456")
    assert user.user_id == admin.user_id
    assert decode_access_token_mfa_verified(pair.access_token) is True


async def test_mfa_pending_carries_persist_session_false():
    from agentcore.security import create_mfa_pending_token, hash_password

    svc, users, creds, tokens, _mfa = _make_admin_with_mfa(enrolled=True)
    admin = await users.create(username="mfaephem", display_name="A", role="admin")
    await creds.create(user_id=admin.user_id, password_hash=hash_password(_PW))
    pending = create_mfa_pending_token(
        admin.user_id, audience="admin", persist_session=False
    )
    _, pair = await svc.complete_mfa_login(pending_token=pending, code="123456")
    assert pair.persist_session is False
    tip = next(iter(tokens.records.values()))
    assert tip.persist_session is False


async def test_admin_password_login_without_mfa_claim_when_not_enrolled(monkeypatch):
    from agentcore.security import decode_access_token_mfa_verified, hash_password

    monkeypatch.setattr(
        "agentcore.auth.service.settings.admin_mfa_required", True
    )
    svc, users, creds, *_rest = _make_admin_with_mfa(enrolled=False)
    admin = await users.create(username="setupadm", display_name="A", role="admin")
    await creds.create(user_id=admin.user_id, password_hash=hash_password(_PW))
    result = await svc.login(username="setupadm", password=_PW, platform="admin")
    assert result.tokens is not None
    assert result.mfa_setup_required is True
    assert decode_access_token_mfa_verified(result.tokens.access_token) is False


async def test_refresh_propagates_mfa_claim_for_enrolled_admin():
    from agentcore.security import (
        create_mfa_pending_token,
        decode_access_token_mfa_verified,
        hash_password,
    )

    svc, users, creds, *_ = _make_admin_with_mfa(enrolled=True)
    admin = await users.create(username="refadm", display_name="A", role="admin")
    await creds.create(user_id=admin.user_id, password_hash=hash_password(_PW))
    pending = create_mfa_pending_token(admin.user_id, audience="admin")
    _, pair = await svc.complete_mfa_login(pending_token=pending, code="123456")
    rotated = await svc.refresh(refresh_token=pair.refresh_token)
    assert decode_access_token_mfa_verified(rotated.access_token) is True



async def test_login_username_is_case_insensitive():
    svc, _u, _c, _t = _make()
    user = await svc.register(username="alice", password=_PW)
    logged, _pair = await _do_login(svc, username="Alice", password=_PW)
    assert logged.user_id == user.user_id


async def test_update_profile_claims_username_from_system_handle():
    svc, users, _c, _t = _make()
    user = await users.create(username="user_a3f90d12", display_name="user_a3f90d12")
    updated = await svc.update_profile(user_id=user.user_id, username="alice")
    assert updated.username == "alice"
    assert updated.username_changed_at is not None


async def test_update_profile_rejects_taken_username():
    svc, users, _c, _t = _make()
    await svc.register(username="bob", password=_PW)
    user = await users.create(username="user_b1c2d3e4", display_name="user_b1c2d3e4")
    with pytest.raises(ValidationError, match="占用"):
        await svc.update_profile(user_id=user.user_id, username="bob")


async def test_update_profile_username_cooldown_after_claim():
    svc, users, _c, _t = _make()
    user = await users.create(username="alice", display_name="Alice")
    users._by_id[user.user_id].username_changed_at = datetime.now(UTC)
    with pytest.raises(ValidationError, match="14"):
        await svc.update_profile(user_id=user.user_id, username="alicia")


async def test_update_profile_rejects_reserved_username():
    svc, users, _c, _t = _make()
    user = await users.create(username="user_c3d4e5f6", display_name="x")
    with pytest.raises(ValidationError, match="不可用"):
        await svc.update_profile(user_id=user.user_id, username="admin")
