"""``scripts/new_dev_user.py``：本机才放行，并且每次都是一个新号。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import scripts.new_dev_user as script
from agentcore.core.errors import ValidationError


def test_refuses_when_debug_is_off():
    with pytest.raises(SystemExit) as raised:
        script.assert_local_debug(debug=False)
    assert raised.value.code == 2


def test_allows_local_debug():
    script.assert_local_debug(debug=True)


def test_generated_password_meets_signup_floor():
    assert len(script.generate_fresh_password()) >= script._MIN_PASSWORD_LENGTH


def test_short_password_is_refused():
    with pytest.raises(SystemExit) as raised:
        script.require_password("short")
    assert raised.value.code == 2


def test_blank_display_name_is_omitted():
    assert script.read_display_name([]) is None
    assert script.read_display_name(["--display-name", "  演练  "]) == "演练"


async def test_allocate_skips_a_taken_handle():
    users = SimpleNamespace(
        get_by_username=AsyncMock(side_effect=[SimpleNamespace(), None])
    )
    with patch.object(
        script,
        "generate_username_handle",
        side_effect=["user_aaaaaaaa", "user_bbbbbbbb"],
    ):
        assert await script.allocate_fresh_username(users) == "user_bbbbbbbb"


async def test_allocate_gives_up_after_the_attempt_budget():
    users = SimpleNamespace(get_by_username=AsyncMock(return_value=SimpleNamespace()))
    with (
        patch.object(script, "generate_username_handle", return_value="user_aaaaaaaa"),
        pytest.raises(ValidationError),
    ):
        await script.allocate_fresh_username(users)
    assert users.get_by_username.await_count == script.ALLOCATE_ATTEMPTS


async def test_mint_creates_a_default_quota_user_then_auto_joins():
    created: dict = {}

    async def _create(**kwargs):
        created.update(kwargs)
        return SimpleNamespace(
            user_id="uid-1",
            username=kwargs["username"],
            display_name=kwargs["display_name"],
            is_unlimited=False,
        )

    users = SimpleNamespace(get_by_username=AsyncMock(return_value=None), create=_create)
    creds = SimpleNamespace(create=AsyncMock())
    messaging = SimpleNamespace(join_auto_join_chats=AsyncMock())
    session = SimpleNamespace(commit=AsyncMock())

    with patch.object(script, "generate_username_handle", return_value="user_aabbccdd"):
        user = await script.mint_fresh_user(
            session=session,
            users=users,
            creds=creds,
            messaging=messaging,
            password="local-pass-1",
        )

    assert user.username == "user_aabbccdd"
    assert created["display_name"] == "user_aabbccdd"
    assert created["role"] == "user"
    assert created["commit"] is False
    assert "email" not in created
    assert "is_unlimited" not in created
    assert user.is_unlimited is False
    password_hash = creds.create.await_args.kwargs["password_hash"]
    assert password_hash != "local-pass-1"
    session.commit.assert_awaited_once()
    messaging.join_auto_join_chats.assert_awaited_once_with(user_id="uid-1")


async def test_mint_keeps_the_account_when_auto_join_fails():
    async def _create(**kwargs):
        return SimpleNamespace(
            user_id="uid-2",
            username=kwargs["username"],
            display_name=kwargs["display_name"],
            is_unlimited=False,
        )

    users = SimpleNamespace(get_by_username=AsyncMock(return_value=None), create=_create)
    session = SimpleNamespace(commit=AsyncMock())
    messaging = SimpleNamespace(
        join_auto_join_chats=AsyncMock(side_effect=RuntimeError("hub down"))
    )

    with patch.object(script, "generate_username_handle", return_value="user_ccddeeff"):
        user = await script.mint_fresh_user(
            session=session,
            users=users,
            creds=SimpleNamespace(create=AsyncMock()),
            messaging=messaging,
            password="local-pass-1",
            display_name="演练",
        )

    assert user.user_id == "uid-2"
    assert user.display_name == "演练"
    session.commit.assert_awaited_once()
