"""Decrypt a stored search API key. Plaintext never leaves this module's callers."""

from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.security.keys import KeyEncryptor

logger = get_logger(__name__)


def search_encryptor() -> KeyEncryptor | None:
    if not settings.encryption_key:
        return None
    try:
        return KeyEncryptor(settings.encryption_key)
    except ValueError:
        logger.error("search.key_malformed")
        return None


def decrypt_search_key(ciphertext: bytes | None) -> str:
    if not ciphertext:
        return ""
    enc = search_encryptor()
    if enc is None:
        return ""
    try:
        return enc.decrypt(ciphertext).decode()
    except Exception:  # noqa: BLE001 — a bad ciphertext is an empty key, not a crash
        logger.warning("search.key_decrypt_failed")
        return ""


def mask_search_key(plaintext: str) -> str | None:
    if not plaintext:
        return None
    if len(plaintext) <= 4:
        return "••••"
    return f"••••{plaintext[-4:]}"
