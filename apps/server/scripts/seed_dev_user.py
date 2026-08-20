"""Seed an idempotent local dev user so you can log in without going through
invite-gated registration.

Run from ``apps/server``::

    uv run python scripts/seed_dev_user.py

Credentials default to ``dev`` / ``devpassword`` but can be overridden via the
``DEV_USERNAME`` / ``DEV_PASSWORD`` env vars. Put the SAME values in the desktop
app's ``.env.local`` (``VITE_DEV_USERNAME`` / ``VITE_DEV_PASSWORD``) to enable
dev auto-login.

This is a dev-only convenience: it creates a REAL user that logs in through the
normal ``/auth/login`` flow, so it never touches the auth code path and carries
no production bypass risk. It does **not** need ``LEGACY_REGISTER_ENABLED``
(that flag only reopens HTTP ``POST /v1/auth/register``). Safe to re-run —
existing users are left untouched.
"""

from __future__ import annotations

import asyncio
import os

from agentcore.db import async_session_factory
from agentcore.db.repositories import CredentialsRepository, UserRepository
from agentcore.security import hash_password

DEFAULT_USERNAME = "dev"
DEFAULT_PASSWORD = "devpassword"  # noqa: S105 - dev seed value, not a real secret
DEFAULT_ROLE = "user"  # mirror a normal account so dev hits real permission paths


async def seed() -> None:
    username = os.environ.get("DEV_USERNAME", DEFAULT_USERNAME)
    password = os.environ.get("DEV_PASSWORD", DEFAULT_PASSWORD)
    role = os.environ.get("DEV_ROLE", DEFAULT_ROLE)

    async with async_session_factory() as session:
        users = UserRepository(session)
        creds = CredentialsRepository(session)

        user = await users.get_by_username(username)
        if user is None:
            user = await users.create(
                username=username,
                display_name="Dev",
                role=role,
            )
            print(f"created user {username!r} (id={user.user_id}, role={role})")
        else:
            print(f"user {username!r} already exists (id={user.user_id})")

        # Repair partial seeds too: a user can exist without credentials.
        if await creds.get_by_user_id(user.user_id) is None:
            await creds.create(
                user_id=user.user_id,
                password_hash=hash_password(password),
            )
            print(f"created credentials for {username!r}")
        else:
            print(f"credentials for {username!r} already exist (unchanged)")

    print(
        "\ndev user ready. Put these in apps/desktop/.env.local for auto-login:\n"
        f"  VITE_DEV_USERNAME={username}\n"
        f"  VITE_DEV_PASSWORD={password}"
    )


if __name__ == "__main__":
    asyncio.run(seed())
