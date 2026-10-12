"""Mint one fresh local user so a maintainer can walk the new-account path again.

Run from ``apps/server``::

    uv run python scripts/new_dev_user.py
    uv run python scripts/new_dev_user.py --display-name 演练

Each run inserts a new row. The handle matches public registration
(``user_`` + 8 hex). The password is random unless ``DEV_FRESH_PASSWORD`` is
set (minimum 8 characters, same floor as signup). Login with the username;
there is no email. Auto-join runs once, the same call registration makes, and
quota stays on the default caps (this script does not mark ``is_unlimited``).

Refuses unless ``DEBUG=true``. That flag is the local-dev switch; production
leaves it off, so this file cannot mint accounts against a production config.
It does not reopen ``POST /v1/auth/register``.

The registration form itself (verification code, age, terms) still lives on
the login page. Local ``DEBUG`` with SMTP unset prints that code in the server
log. See docs/02-架构/本地开发.md#初始账号.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.auth.usernames import ALLOCATE_ATTEMPTS, generate_username_handle
from agentcore.config import settings
from agentcore.core.errors import ValidationError
from agentcore.core.logging import get_logger
from agentcore.db import async_session_factory
from agentcore.db.models import User
from agentcore.db.repositories import (
    ChatRepository,
    CredentialsRepository,
    UserBlockRepository,
    UserDirectoryRepository,
    UserRepository,
)
from agentcore.messaging.service import MessagingService
from agentcore.security import hash_password

logger = get_logger(__name__)

_MIN_PASSWORD_LENGTH = 8


def assert_local_debug(*, debug: bool) -> None:
    """Fail closed when this process is not a local debug config."""
    if debug:
        return
    print(
        "refusing: new_dev_user.py only runs when DEBUG=true (local). "
        "It does not mint accounts on a production config.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def generate_fresh_password() -> str:
    return secrets.token_urlsafe(12)


def require_password(password: str) -> str:
    if len(password) < _MIN_PASSWORD_LENGTH:
        print(
            f"password must be at least {_MIN_PASSWORD_LENGTH} characters",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return password


def read_display_name(argv: list[str] | None = None) -> str | None:
    parser = argparse.ArgumentParser(
        description="Mint a fresh local user (DEBUG=true only)."
    )
    parser.add_argument(
        "--display-name",
        default="",
        help="Nickname shown in chats. Empty uses the generated handle.",
    )
    name = parser.parse_args(argv).display_name.strip()
    return name or None


async def allocate_fresh_username(users: UserRepository) -> str:
    for _ in range(ALLOCATE_ATTEMPTS):
        candidate = generate_username_handle()
        if await users.get_by_username(candidate) is None:
            return candidate
    raise ValidationError("注册繁忙，请稍后重试")


async def mint_fresh_user(
    *,
    session: AsyncSession,
    users: UserRepository,
    creds: CredentialsRepository,
    messaging: MessagingService,
    password: str,
    display_name: str | None = None,
) -> User:
    """Insert one unused account and enroll it in auto-join chats.

    The user row commits before auto-join. A messaging failure is logged and
    does not roll the account back — the same split as ``/register/verify``.
    """
    username = await allocate_fresh_username(users)
    nickname = (display_name or "").strip()
    user = await users.create(
        username=username,
        display_name=nickname if nickname else username,
        role="user",
        commit=False,
    )
    await creds.create(
        user_id=user.user_id,
        password_hash=hash_password(password),
        commit=False,
    )
    await session.commit()
    try:
        await messaging.join_auto_join_chats(user_id=user.user_id)
    except Exception:
        logger.warning("chat.auto_join_failed", user=user.user_id, exc_info=True)
    return user


async def _run(password: str, display_name: str | None) -> User:
    async with async_session_factory() as session:
        users = UserRepository(session)
        messaging = MessagingService(
            users=users,
            chats=ChatRepository(session),
            blocks=UserBlockRepository(session),
            directory=UserDirectoryRepository(session),
        )
        return await mint_fresh_user(
            session=session,
            users=users,
            creds=CredentialsRepository(session),
            messaging=messaging,
            password=password,
            display_name=display_name,
        )


def main(argv: list[str] | None = None) -> None:
    assert_local_debug(debug=settings.debug)
    display_name = read_display_name(argv)
    password = require_password(os.environ.get("DEV_FRESH_PASSWORD") or generate_fresh_password())
    user = asyncio.run(_run(password, display_name))
    print(f"created user {user.username!r} (id={user.user_id})")
    print(f"password: {password}")
    print("log in with the username.")
    print("auto-join ran once, same as registration.")
    print("quota uses the default daily and monthly caps.")
    print(
        "if desktop .env.local sets VITE_DEV_USERNAME, restart still enters that "
        "seed account. Unset it, or sign in from a private browser window."
    )


if __name__ == "__main__":
    main()
