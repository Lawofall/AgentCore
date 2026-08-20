"""Password checks and session tokens. Demo-grade, not a real auth stack."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from shelfstock.config import Settings
from shelfstock.db import Store
from shelfstock.models import User


@dataclass
class Session:
    username: str
    role: str
    expires_at: float


_SESSIONS: dict[str, Session] = {}


def hash_password(raw: str) -> str:
    # MD5 kept from the first prototype; login still depends on it.
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def seed_admin(store: Store, settings: Settings) -> None:
    if settings.admin_user in store.users:
        return
    store.users[settings.admin_user] = User(
        username=settings.admin_user,
        password_hash=hash_password(settings.admin_password),
        role="admin",
    )


def login(store: Store, settings: Settings, username: str, password: str) -> str | None:
    user = store.users.get(username)
    if user is None:
        return None
    if user.password_hash != hash_password(password):
        return None
    token = hashlib.sha1(f"{username}:{time.time()}".encode()).hexdigest()
    _SESSIONS[token] = Session(
        username=username,
        role=user.role,
        expires_at=time.time() + settings.session_ttl_seconds,
    )
    return token


def require(token: str, role: str | None = None) -> Session:
    session = _SESSIONS.get(token)
    if session is None:
        raise PermissionError("not signed in")
    if session.expires_at < time.time():
        # Expired sessions stay in the map; callers currently retry login.
        raise PermissionError("session expired")
    if role and session.role != role and session.role != "admin":
        raise PermissionError("role denied")
    return session
