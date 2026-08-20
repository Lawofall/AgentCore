"""Unit tests for EmailAuthService (in-memory fakes, no DB)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agentcore.auth.email_service import EmailAuthService
from agentcore.config import settings
from agentcore.core.errors import AuthorizationError, ValidationError
from agentcore.core.types import new_id
from agentcore.mail.sender import EmailSendError
from agentcore.security import hash_password, verify_password
from tests.test_auth_service import FakeCredentials, FakeRefreshTokens, FakeUsers

_PW = "password123"


class RecordingMailer:
    def __init__(self) -> None:
        self.sends: list[SimpleNamespace] = []

    async def send_verification_code(self, *, to, purpose, code, ttl_seconds):
        self.sends.append(
            SimpleNamespace(to=to, purpose=purpose, code=code, ttl_seconds=ttl_seconds)
        )

    @property
    def last_code(self) -> str:
        return self.sends[-1].code


class FakePending:
    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}

    async def get_by_email(self, email):
        return self.rows.get(email)

    async def get_unexpired_by_username(self, username, *, now):
        return next(
            (
                r
                for r in self.rows.values()
                if r.username == username and r.expires_at > now
            ),
            None,
        )

    async def delete_expired(self, *, now, commit=True):
        self.rows = {k: v for k, v in self.rows.items() if v.expires_at > now}
        return 0

    async def upsert(
        self,
        *,
        email,
        username,
        password_hash,
        display_name,
        registration_ip,
        expires_at,
        commit=True,
    ):
        row = SimpleNamespace(
            email=email,
            username=username,
            password_hash=password_hash,
            display_name=display_name,
            registration_ip=registration_ip,
            expires_at=expires_at,
        )
        self.rows[email] = row
        return row

    async def delete_by_email(self, email, *, commit=True):
        self.rows.pop(email, None)


class FakeChallenges:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], SimpleNamespace] = {}

    async def get(self, *, purpose, email):
        return self.rows.get((purpose, email))

    async def upsert(
        self,
        *,
        purpose,
        email,
        code_hash,
        expires_at,
        user_id=None,
        commit=True,
    ):
        row = SimpleNamespace(
            id=new_id(),
            purpose=purpose,
            email=email,
            user_id=user_id,
            code_hash=code_hash,
            expires_at=expires_at,
            attempt_count=0,
            consumed_at=None,
        )
        self.rows[(purpose, email)] = row
        return row

    async def record_failure(self, challenge_id, *, max_attempts, commit=True):
        row = next(r for r in self.rows.values() if r.id == challenge_id)
        row.attempt_count += 1
        if row.attempt_count >= max_attempts:
            row.consumed_at = datetime.now(UTC)
        return row.attempt_count

    async def consume(self, challenge_id, *, commit=True):
        row = next(r for r in self.rows.values() if r.id == challenge_id)
        row.consumed_at = datetime.now(UTC)


def _make():
    users = FakeUsers()
    creds = FakeCredentials()
    tokens = FakeRefreshTokens()
    pending = FakePending()
    challenges = FakeChallenges()
    mailer = RecordingMailer()
    svc = EmailAuthService(
        users=users,
        credentials=creds,
        refresh_tokens=tokens,
        pending=pending,
        challenges=challenges,
        mailer=mailer,
        session=None,
    )
    return svc, users, creds, tokens, pending, challenges, mailer


@pytest.fixture(autouse=True)
def _open_and_unthrottled(monkeypatch):
    monkeypatch.setattr(settings, "registration_open", True)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "require_email_verified", False)


async def test_start_registration_validates_before_send():
    svc, _u, _c, _t, _p, _ch, mailer = _make()
    with pytest.raises(ValidationError, match="密码"):
        await svc.start_registration(password="short", email="new@example.com")
    assert mailer.sends == []


async def test_start_registration_rejects_taken_email():
    svc, users, _c, _t, _p, _ch, mailer = _make()
    await users.create(username="ada", email="ada@example.com")
    with pytest.raises(ValidationError, match="邮箱"):
        await svc.start_registration(password=_PW, email="ada@example.com")
    assert mailer.sends == []


async def test_register_verify_creates_verified_account():
    svc, users, creds, _t, pending, challenges, mailer = _make()
    ttl = await svc.start_registration(
        password=_PW,
        email="Ada@Example.com",
        registration_ip="203.0.113.9",
    )
    assert ttl == settings.email_code_ttl_seconds
    assert len(mailer.sends) == 1
    staged = pending.rows["ada@example.com"]
    assert staged.username.startswith("user_")
    assert "@" not in staged.username
    assert staged.display_name == ""
    assert staged.username != "ada"
    assert not staged.username.startswith("ada")
    user = await svc.verify_registration(email="ada@example.com", code=mailer.last_code)
    assert user.username == staged.username
    assert user.display_name == staged.username
    assert user.email == "ada@example.com"
    assert user.email_verified_at is not None
    assert user.registration_ip == "203.0.113.9"
    stored = await creds.get_by_user_id(user.user_id)
    assert stored is not None and verify_password(_PW, stored.password_hash)
    assert pending.rows == {}
    challenge = await challenges.get(purpose="register", email="ada@example.com")
    assert challenge.consumed_at is not None


async def test_register_verify_rejects_wrong_code_then_invalidates():
    svc, _u, _c, _t, _p, _ch, mailer = _make()
    await svc.start_registration(password=_PW, email="bob@example.com")
    for _ in range(settings.email_code_max_attempts - 1):
        with pytest.raises(ValidationError, match="无效或已过期"):
            await svc.verify_registration(email="bob@example.com", code="000000")
    with pytest.raises(ValidationError, match="错误次数过多"):
        await svc.verify_registration(email="bob@example.com", code="000000")
    with pytest.raises(ValidationError, match="无效或已过期"):
        await svc.verify_registration(email="bob@example.com", code=mailer.last_code)


async def test_register_verify_is_one_time():
    svc, _u, _c, _t, _p, _ch, mailer = _make()
    await svc.start_registration(password=_PW, email="cam@example.com")
    code = mailer.last_code
    await svc.verify_registration(email="cam@example.com", code=code)
    with pytest.raises(ValidationError, match="无效或已过期"):
        await svc.verify_registration(email="cam@example.com", code=code)


async def test_register_verify_rejects_expired_code():
    svc, _u, _c, _t, _p, challenges, mailer = _make()
    await svc.start_registration(password=_PW, email="dot@example.com")
    row = await challenges.get(purpose="register", email="dot@example.com")
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ValidationError, match="无效或已过期"):
        await svc.verify_registration(email="dot@example.com", code=mailer.last_code)


async def test_start_registration_send_failure_is_validation_error():
    svc, _u, _c, _t, pending, challenges, _m = _make()

    class _Boom:
        async def send_verification_code(self, **_kwargs):
            raise EmailSendError("smtp send failed")

    svc._mailer = _Boom()
    with pytest.raises(ValidationError, match="发送失败"):
        await svc.start_registration(password=_PW, email="ada@example.com")
    assert "ada@example.com" in pending.rows
    assert await challenges.get(purpose="register", email="ada@example.com") is not None


async def test_start_registration_closed(monkeypatch):
    monkeypatch.setattr(settings, "registration_open", False)
    svc, *_ = _make()
    with pytest.raises(AuthorizationError, match="注册已关闭"):
        await svc.start_registration(password=_PW, email="eve@example.com")


async def test_pending_handles_do_not_block_other_email():
    svc, _u, _c, _t, pending, _ch, mailer = _make()
    await svc.start_registration(password=_PW, email="fin@example.com")
    await svc.start_registration(password=_PW, email="other@example.com")
    assert len(mailer.sends) == 2
    first = pending.rows["fin@example.com"].username
    second = pending.rows["other@example.com"].username
    assert first != second
    assert first.startswith("user_") and second.startswith("user_")


async def test_start_registration_resend_reuses_handle():
    svc, _u, _c, _t, pending, _ch, mailer = _make()
    await svc.start_registration(password=_PW, email="fin@example.com")
    handle = pending.rows["fin@example.com"].username
    await svc.start_registration(password=_PW, email="fin@example.com")
    assert pending.rows["fin@example.com"].username == handle
    assert len(mailer.sends) == 2


async def test_verify_reallocates_if_handle_taken_meanwhile():
    svc, users, _c, _t, pending, _ch, mailer = _make()
    await svc.start_registration(password=_PW, email="new@example.com")
    handle = pending.rows["new@example.com"].username
    await users.create(username=handle, display_name="taken")
    user = await svc.verify_registration(email="new@example.com", code=mailer.last_code)
    assert user.username != handle
    assert user.username.startswith("user_")
    assert user.display_name == user.username


async def test_start_registration_retries_taken_generated_handle(monkeypatch):
    from agentcore.auth import email_service as email_service_mod

    handles = iter(["user_taken1", "user_ok0001"])
    monkeypatch.setattr(
        email_service_mod, "generate_username_handle", lambda: next(handles)
    )
    svc, users, _c, _t, pending, _ch, _m = _make()
    await users.create(username="user_taken1", display_name="taken")
    await svc.start_registration(password=_PW, email="new@example.com")
    assert pending.rows["new@example.com"].username == "user_ok0001"


async def test_password_forgot_always_accepted_unknown_email():
    svc, _u, _c, _t, _p, _ch, mailer = _make()
    ttl = await svc.start_password_reset(email="ghost@example.com")
    assert ttl == settings.email_code_ttl_seconds
    assert mailer.sends == []


async def test_password_forgot_send_failure_matches_unknown_email():
    """SMTP outage must not distinguish a live inbox from a missing one."""
    svc, users, _c, _t, _p, _ch, _m = _make()
    await users.create(username="gia", email="gia@example.com")

    class _Boom:
        async def send_verification_code(self, **_kwargs):
            raise EmailSendError("smtp send failed")

    svc._mailer = _Boom()
    known = await svc.start_password_reset(email="gia@example.com")
    unknown = await svc.start_password_reset(email="ghost@example.com")
    assert known == unknown == settings.email_code_ttl_seconds


async def test_password_reset_rotates_hash_and_stamps_verified():
    svc, users, creds, tokens, _p, _ch, mailer = _make()
    user = await users.create(username="gia", email="gia@example.com")
    await creds.create(user_id=user.user_id, password_hash=hash_password(_PW))
    await tokens.create(
        user_id=user.user_id,
        token_hash="old",
        token_family="fam",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    await svc.start_password_reset(email="gia@example.com")
    await svc.reset_password(
        email="gia@example.com", code=mailer.last_code, new_password="newpass99"
    )
    stored = await creds.get_by_user_id(user.user_id)
    assert verify_password("newpass99", stored.password_hash)
    assert user.email_verified_at is not None
    assert all(r.revoked_at is not None for r in tokens.records.values())


async def test_password_reset_unknown_code_does_not_reveal():
    svc, *_ = _make()
    with pytest.raises(ValidationError, match="无效或已过期"):
        await svc.reset_password(
            email="ghost@example.com", code="123456", new_password="newpass99"
        )


async def test_email_verify_sets_address_and_timestamp():
    svc, users, _c, _t, _p, _ch, mailer = _make()
    user = await users.create(username="hal")
    await svc.start_email_verification(user_id=user.user_id, email="hal@example.com")
    updated = await svc.verify_email(
        user_id=user.user_id, email="hal@example.com", code=mailer.last_code
    )
    assert updated.email == "hal@example.com"
    assert updated.email_verified_at is not None


async def test_email_verify_rejects_other_users_code():
    svc, users, _c, _t, _p, _ch, mailer = _make()
    owner = await users.create(username="ivy")
    thief = await users.create(username="jay")
    await svc.start_email_verification(user_id=owner.user_id, email="ivy@example.com")
    with pytest.raises(ValidationError, match="无效或已过期"):
        await svc.verify_email(
            user_id=thief.user_id, email="ivy@example.com", code=mailer.last_code
        )


async def test_email_verify_rejects_taken_inbox():
    svc, users, _c, _t, _p, _ch, mailer = _make()
    await users.create(username="kim", email="taken@example.com")
    me = await users.create(username="leo")
    with pytest.raises(ValidationError, match="邮箱"):
        await svc.start_email_verification(user_id=me.user_id, email="taken@example.com")
    assert mailer.sends == []


async def test_register_verify_uses_optional_display_name():
    svc, _u, _c, _t, pending, _ch, mailer = _make()
    await svc.start_registration(password=_PW, email="nick@example.com")
    handle = pending.rows["nick@example.com"].username
    user = await svc.verify_registration(
        email="nick@example.com",
        code=mailer.last_code,
        display_name="Nick Chen",
    )
    assert user.username == handle
    assert user.display_name == "Nick Chen"
