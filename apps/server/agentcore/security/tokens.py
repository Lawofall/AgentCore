"""JWT access, inference, folders, account, and preview tokens (python-jose)."""

from datetime import UTC, datetime, timedelta
from typing import Literal, NamedTuple

from jose import JWTError, jwt

from agentcore.config import settings
from agentcore.core.errors import AuthenticationError

_JWT_ALGORITHM = "HS256"

TokenAudience = Literal["product", "admin"]
AccessTokenClaims = tuple[str, TokenAudience]
# (user_id, audience, persist_session) — persist defaults True for legacy pending JWTs.
MfaPendingClaims = tuple[str, TokenAudience, bool]


def create_access_token(
    user_id: str,
    *,
    audience: TokenAudience,
    family: str | None = None,
    mfa_verified: bool = False,
    expires_delta: timedelta | None = None,
) -> str:
    """Mint a short-lived access JWT carrying ``user_id`` as the subject.

    ``family`` (claim ``fam``) binds the access token to its refresh-token family
    so session-management endpoints can mark the current device. Omitted only for
    legacy test helpers; production issuance always passes it.

    ``mfa_verified`` (claim ``mfa``) proves this session completed admin MFA; set
    only after ``complete_mfa_login`` (or a refresh of such a family).
    """
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes))
    claims: dict = {
        "sub": user_id,
        "type": "access",
        "session": audience,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if family is not None:
        claims["fam"] = family
    if mfa_verified:
        claims["mfa"] = True
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> str:
    """Return the subject (user_id) of a valid access token.

    Raises ``AuthenticationError`` for any invalid, tampered, expired, or
    wrong-type token so callers can translate it to a 401 uniformly.
    """
    user_id, _aud = decode_access_token_claims(token)
    return user_id


def decode_access_token_claims(token: str) -> AccessTokenClaims:
    """Return ``(user_id, audience)`` from a valid access token."""
    claims = _decode_access_claims(token)
    return claims["sub"], claims["session"]


def decode_access_token_family(token: str) -> str | None:
    """Return the ``fam`` claim if present; ``None`` for legacy tokens without it.

    Still validates the token (type / signature / expiry) so callers can trust a
    missing fam as "old token", not "invalid token".
    """
    claims = _decode_access_claims(token)
    fam = claims.get("fam")
    return fam if isinstance(fam, str) and fam else None


def decode_access_token_mfa_verified(token: str) -> bool:
    """Return whether the access token carries ``mfa: true`` (admin MFA session proof)."""
    claims = _decode_access_claims(token)
    return claims.get("mfa") is True


def _decode_access_claims(token: str) -> dict:
    try:
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired token") from exc

    if claims.get("type") != "access":
        raise AuthenticationError("Wrong token type")
    sub = claims.get("sub")
    if not sub:
        raise AuthenticationError("Token missing subject")
    aud = claims.get("session")
    if aud not in ("product", "admin"):
        raise AuthenticationError("Token missing session audience")
    return claims


def create_mfa_pending_token(
    user_id: str,
    *,
    audience: TokenAudience,
    persist_session: bool = True,
    expires_delta: timedelta | None = None,
) -> str:
    """Short-lived token gating the second login factor (password already verified)."""
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=5))
    claims = {
        "sub": user_id,
        "type": "mfa_pending",
        "session": audience,
        # Carry login persist_session through MFA so cookie/TTL policy is unchanged.
        "persist_session": persist_session,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def decode_mfa_pending_token(token: str) -> MfaPendingClaims:
    """Return ``(user_id, audience, persist_session)`` from a valid MFA-pending token."""
    try:
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired MFA session") from exc

    if claims.get("type") != "mfa_pending":
        raise AuthenticationError("Wrong token type")
    sub = claims.get("sub")
    if not sub:
        raise AuthenticationError("Token missing subject")
    aud = claims.get("session")
    if aud not in ("product", "admin"):
        raise AuthenticationError("Token missing session audience")
    persist = claims.get("persist_session", True)
    if not isinstance(persist, bool):
        persist = True
    return sub, aud, persist


def create_inference_token(user_id: str, *, expires_delta: timedelta | None = None) -> str:
    """Mint a scoped token authorizing a local sidecar's LLM calls through the cloud
    inference proxy (双模式工作区 §一.1 / Slice 4a).

    The desktop authenticates via httpOnly cookies, so the renderer can never hand
    the sidecar a usable bearer; instead it exchanges its cookie for THIS token and
    passes it to the on-machine engine, which sends it as ``Authorization: Bearer``
    to ``/v1/inference``. A distinct ``type`` ("inference", not "access") means it
    can ONLY authorize the inference proxy — :func:`decode_inference_token` refuses
    an access token and :func:`decode_access_token` refuses this one, so the two kinds
    can never be confused (a leaked inference token can't drive the cookie-auth API).
    """
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.inference_token_expire_minutes))
    claims = {
        "sub": user_id,
        "type": "inference",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def decode_inference_token(token: str) -> str:
    """Return the subject (user_id) of a valid inference token (Slice 4a).

    Raises ``AuthenticationError`` for any invalid, tampered, expired, or
    wrong-type token (including a regular ``access`` token), so the proxy
    translates it to a 401 uniformly.
    """
    try:
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired inference token") from exc

    if claims.get("type") != "inference":
        raise AuthenticationError("Wrong token type")
    sub = claims.get("sub")
    if not sub:
        raise AuthenticationError("Token missing subject")
    return sub


def create_folders_token(user_id: str, *, expires_delta: timedelta | None = None) -> str:
    """Mint a scoped token authorizing sidecar cloud folder roster calls.

    Desktop exchanges its cookie session for THIS token and passes
    ``{baseUrl, apiKey}`` into the on-machine engine (same shape as inference).
    Distinct ``type`` ("folders") means it can ONLY authorize account folders
    read/write on the narrow folders surface — never access-cookie APIs and never
    the inference proxy. ``decode_folders_token`` / ``decode_access_token`` /
    ``decode_inference_token`` refuse each other's types.
    """
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.folders_token_expire_minutes))
    claims = {
        "sub": user_id,
        "type": "folders",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def decode_folders_token(token: str) -> str:
    """Return the subject (user_id) of a valid folders narrow token.

    Raises ``AuthenticationError`` for any invalid, tampered, expired, or
    wrong-type token (including ``access`` and ``inference``).
    """
    try:
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired folders token") from exc

    if claims.get("type") != "folders":
        raise AuthenticationError("Wrong token type")
    sub = claims.get("sub")
    if not sub:
        raise AuthenticationError("Token missing subject")
    return sub


def create_account_token(user_id: str, *, expires_delta: timedelta | None = None) -> str:
    """Mint a scoped token for sidecar cloud conversation-log access (R3a).

    Desktop exchanges its cookie session for THIS token and passes
    ``accountAuth: {baseUrl, apiKey}`` into the on-machine engine (same shape as
    folders). ``baseUrl`` is the account API root (``…/v1/account``); ``apiKey``
    is this JWT. Distinct ``type`` ("account") means it can ONLY authorize the
    narrow conversation-log surface (``search`` / ``read``) — never folders
    roster, never inference proxy, never cookie-auth UI CRUD.
    ``decode_account_token`` / ``decode_access_token`` / ``decode_inference_token``
    / ``decode_folders_token`` refuse each other's types.
    """
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.account_token_expire_minutes))
    claims = {
        "sub": user_id,
        "type": "account",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def decode_account_token(token: str) -> str:
    """Return the subject (user_id) of a valid account narrow token.

    Raises ``AuthenticationError`` for any invalid, tampered, expired, or
    wrong-type token (including ``access``, ``inference``, and ``folders``).
    """
    try:
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired account token") from exc

    if claims.get("type") != "account":
        raise AuthenticationError("Wrong token type")
    sub = claims.get("sub")
    if not sub:
        raise AuthenticationError("Token missing subject")
    return sub


class PreviewTokenClaims(NamedTuple):
    """Decoded ``type=preview`` JWT: who, which conversation, which process, which port."""

    user_id: str
    conversation_id: str
    process_id: str
    port: int


def create_preview_token(
    user_id: str,
    *,
    conversation_id: str,
    process_id: str,
    port: int,
    expires_delta: timedelta | None = None,
) -> str:
    """Mint a short-lived ticket for the cloud user-preview URL (安全 · 五、第二刀).

    Desktop / web exchanges the current access session for THIS token and opens
    the preview origin with it. Distinct ``type`` ("preview") means it can ONLY
    authorize that origin — never cookie-auth APIs, inference, folders, or
    account logs. ``decode_preview_token`` / the other decoders refuse each
    other's types.

    Claims: ``sub`` (user_id), ``cid`` (conversation_id), ``pid`` (process_id),
    ``port`` (guest HTTP port). Never log the JWT; it is a capability ticket,
    not a session cookie — do not put it in model context or the journal.
    """
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.preview_token_expire_minutes))
    claims = {
        "sub": user_id,
        "type": "preview",
        "cid": conversation_id,
        "pid": process_id,
        "port": port,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def decode_preview_token(token: str) -> PreviewTokenClaims:
    """Return ``(user_id, conversation_id, process_id, port)`` from a valid preview JWT.

    Raises ``AuthenticationError`` for any invalid, tampered, expired, or
    wrong-type token (including ``access``, ``inference``, ``folders``, and
    ``account``).
    """
    try:
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired preview token") from exc

    if claims.get("type") != "preview":
        raise AuthenticationError("Wrong token type")
    sub = claims.get("sub")
    if not sub or not isinstance(sub, str):
        raise AuthenticationError("Token missing subject")
    cid = claims.get("cid")
    if not cid or not isinstance(cid, str):
        raise AuthenticationError("Token missing conversation")
    pid = claims.get("pid")
    if not pid or not isinstance(pid, str):
        raise AuthenticationError("Token missing process")
    port = claims.get("port")
    # ``bool`` is an ``int`` subclass — a JSON ``true`` must not pass as a port.
    if type(port) is not int or not (1 <= port <= 65535):
        raise AuthenticationError("Token missing port")
    return PreviewTokenClaims(sub, cid, pid, port)
