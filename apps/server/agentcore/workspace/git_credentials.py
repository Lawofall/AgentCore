"""Account-level Git credentials (G3 · 云私仓 PAT).

Stores one encrypted PAT per user (AES-256-GCM via ``KeyEncryptor``, same wire
as BYOK). Tools never accept password parameters — clone / push load plaintext
only inside the server process for cloud http(s) remotes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlparse, urlunparse

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.config import settings
from agentcore.core.errors import KeyStorageUnavailableError, ValidationError
from agentcore.core.logging import get_logger
from agentcore.db.models import UserGitCredential
from agentcore.security.keys import KeyEncryptor

logger = get_logger(__name__)

_DEFAULT_USERNAME = "x-access-token"


@dataclass(frozen=True)
class GitCredentialView:
    """Settings view — never the plaintext token."""

    configured: bool
    masked_token: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class GitAuthMaterial:
    """In-process auth for clone/push — never serialize or log."""

    username: str
    token: str


def _encryptor() -> KeyEncryptor | None:
    key = (settings.encryption_key or "").strip()
    if not key:
        return None
    try:
        return KeyEncryptor(key)
    except ValueError:
        return None


def _mask_token(token: str) -> str:
    token = token.strip()
    if len(token) <= 4:
        return "••••"
    return f"••••{token[-4:]}"


def embed_http_basic_auth(repo_url: str, *, username: str, token: str) -> str:
    """Return ``https://user:token@host/path`` for a validated http(s) URL.

    Caller must already have passed scheme / SSRF policy on the bare URL.
    Never log the return value.
    """
    parsed = urlparse(repo_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("仅支持 http(s) 协议的仓库地址")
    user = quote(username.strip() or _DEFAULT_USERNAME, safe="")
    password = quote(token.strip(), safe="")
    host = parsed.hostname
    netloc = f"{user}:{password}@{host}"
    if parsed.port:
        netloc = f"{user}:{password}@{host}:{parsed.port}"
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


class GitCredentialService:
    """Upsert / read / delete the account Git PAT."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_view(self, user_id: str) -> GitCredentialView:
        row = await self._session.get(UserGitCredential, user_id)
        if row is None:
            return GitCredentialView(configured=False)
        enc = _encryptor()
        masked: str | None = None
        if enc is not None and row.token_enc:
            try:
                masked = _mask_token(enc.decrypt(row.token_enc).decode())
            except Exception:  # noqa: BLE001 — corrupt ciphertext → still configured
                masked = "••••"
        return GitCredentialView(
            configured=True,
            masked_token=masked,
            updated_at=row.updated_at,
        )

    async def upsert(self, user_id: str, *, token: str) -> GitCredentialView:
        token = token.strip()
        if not token:
            raise ValidationError("PAT 不能为空")
        if len(token) > 500:
            raise ValidationError("PAT 过长")
        enc = _encryptor()
        if enc is None:
            raise KeyStorageUnavailableError("凭据加密密钥未配置，无法保存 Git PAT。请联系管理员。")
        ciphertext = enc.encrypt(token.encode())
        row = await self._session.get(UserGitCredential, user_id)
        now = datetime.now(UTC)
        if row is None:
            row = UserGitCredential(
                user_id=user_id,
                token_enc=ciphertext,
                username=_DEFAULT_USERNAME,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.token_enc = ciphertext
            row.username = _DEFAULT_USERNAME
            row.updated_at = now
        await self._session.commit()
        logger.info("git_credential.upserted", user_id=user_id)
        return await self.get_view(user_id)

    async def delete(self, user_id: str) -> None:
        await self._session.execute(
            delete(UserGitCredential).where(UserGitCredential.user_id == user_id)
        )
        await self._session.commit()
        logger.info("git_credential.deleted", user_id=user_id)


async def load_git_auth(session: AsyncSession, user_id: str) -> GitAuthMaterial | None:
    """Decrypt account PAT for in-process use; ``None`` if unset / undecryptable."""
    if not user_id:
        return None
    enc = _encryptor()
    if enc is None:
        return None
    row = await session.get(UserGitCredential, user_id)
    if row is None or not row.token_enc:
        return None
    try:
        token = enc.decrypt(row.token_enc).decode()
    except Exception:  # noqa: BLE001
        logger.warning("git_credential.decrypt_failed", user_id=user_id)
        return None
    if not token.strip():
        return None
    return GitAuthMaterial(
        username=(row.username or _DEFAULT_USERNAME).strip() or _DEFAULT_USERNAME,
        token=token.strip(),
    )


async def load_git_auth_for_user(user_id: str) -> GitAuthMaterial | None:
    """Open a short-lived session and decrypt — for tools without a request session.

    Fail-soft: missing table / DB / decrypt → ``None`` (caller proceeds without PAT).
    """
    if not user_id:
        return None
    from agentcore.db.base import async_session_factory

    try:
        async with async_session_factory() as session:
            return await load_git_auth(session, user_id)
    except Exception:  # noqa: BLE001 — tools must not hard-fail on credential lookup
        logger.debug("git_credential.load_failed", user_id=user_id)
        return None


async def delete_git_credentials_for_user(
    session: AsyncSession, user_id: str, *, commit: bool = True
) -> None:
    """注销 cascade — drop ciphertext so it does not outlive the account."""
    await session.execute(delete(UserGitCredential).where(UserGitCredential.user_id == user_id))
    if commit:
        await session.commit()
    else:
        await session.flush()
