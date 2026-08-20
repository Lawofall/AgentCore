"""Auth and profile request/response schemas."""

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ._helpers import _avatar_url

if TYPE_CHECKING:
    from agentcore.db.models import User


class RegisterRequest(BaseModel):
    username: str = Field(
        ...,
        min_length=3,
        max_length=100,
        pattern=r"^[^@]+$",
        description=(
            "Handle only. Must not contain @ "
            "(reserved so login can tell email from username)."
        ),
    )
    password: str = Field(..., min_length=8, max_length=256)
    display_name: str | None = Field(None, max_length=200)
    # Plain string (no email-validator dependency). Public signup uses
    # RegisterSendCodeRequest where email is required.
    email: str | None = Field(None, max_length=255)


class RegisterSendCodeRequest(BaseModel):
    """Public signup: inbox + password. Username is allocated server-side."""

    password: str = Field(..., min_length=8, max_length=256)
    email: str = Field(..., min_length=3, max_length=255)


class EmailCodeRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class RegisterVerifyRequest(BaseModel):
    """Finish verify-then-create signup; optional nickname only here."""

    email: str = Field(..., min_length=3, max_length=255)
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    display_name: str | None = Field(None, max_length=200)


class PasswordForgotRequest(BaseModel):
    # min_length=1 so a junk shape still hits the 202 anti-enumeration path.
    email: str = Field(..., min_length=1, max_length=255)


class PasswordResetRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(..., min_length=8, max_length=256)


class EmailSendCodeRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)


class EmailCodeAcceptedResponse(BaseModel):
    """202 body for every send-code path (including password-forgot)."""

    status: Literal["accepted"] = "accepted"
    expires_in: int = Field(..., description="Code TTL in seconds")


class LoginRequest(BaseModel):
    username: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description=(
            "Email or username. Contains @ → normalized email lookup; "
            "otherwise username lookup. Field name is unchanged on purpose."
        ),
    )
    password: str = Field(..., min_length=1, max_length=256)
    persist_session: bool = Field(
        True,
        description=(
            "true: persistent cookies + long refresh TTL. "
            "false: session cookies (no Max-Age) + short absolute refresh ceiling; "
            "bearer still returns tokens with short refresh_expires_in."
        ),
    )


class LoginMfaRequest(BaseModel):
    pending_token: str = Field(..., min_length=1, max_length=2048)
    code: str | None = Field(None, min_length=6, max_length=8)
    recovery_code: str | None = Field(None, min_length=8, max_length=16)


class LoginResponse(BaseModel):
    """Cookie/bearer login outcome — MFA may defer token issuance."""

    user: "UserResponse | None" = None
    mfa_required: bool = False
    pending_token: str | None = None
    mfa_setup_required: bool = False


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaConfirmRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=8)


class MfaConfirmResponse(BaseModel):
    recovery_codes: list[str]


class MfaStatusResponse(BaseModel):
    enrolled: bool
    required: bool = True


class ChangePasswordRequest(BaseModel):
    """Self-service password change (修改密码): the current password proves intent,
    the new one is validated server-side (same ≥8 policy as registration)."""

    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)


class UpdateProfileRequest(BaseModel):
    """Patch the signed-in user's profile (个人资料编辑). Both fields optional — only
    those present are changed; an explicit ``null`` email clears it. ``display_name``
    must be non-empty when present (enforced in the service)."""

    display_name: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=255)
    username: str | None = Field(
        None,
        min_length=3,
        max_length=32,
        pattern=r"^[a-zA-Z0-9](?:[a-zA-Z0-9._-]*[a-zA-Z0-9])?$",
        description="Self-selected handle; stored lowercase after claim.",
    )


class DeleteAccountRequest(BaseModel):
    """Self-service account deletion (注销账户): the password re-confirms a
    destructive, irreversible action before the account is soft-deleted + anonymized."""

    password: str = Field(..., min_length=1, max_length=256)


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None
    email_verified_at: datetime | None = None
    role: str
    created_at: datetime
    # Served avatar URL (头像) derived from the stored object key, e.g.
    # ``/v1/users/<id>/avatar?v=<hash>``; None = no avatar. A relative path on
    # purpose — the backend is agnostic of its public origin, so the client prefixes
    # its API base. The ``?v=`` is a content hash, so the cached <img> refreshes on
    # change. → see api/routes/users.py for the (public) serving endpoint.
    avatar_url: str | None = None
    # True when an admin reset handed a one-off temp password — the client should
    # force a self-service password change before normal use.
    password_must_change: bool = False

    @classmethod
    def from_user(cls, user: "User", *, password_must_change: bool = False) -> "UserResponse":
        """Build the API view of a user row (the single source for this mapping)."""
        return cls(
            id=user.user_id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            email_verified_at=getattr(user, "email_verified_at", None),
            role=user.role,
            created_at=user.created_at,
            avatar_url=_avatar_url(user.user_id, user.avatar_key),
            password_must_change=password_must_change,
        )


class TokenResponse(BaseModel):
    """Bearer-token bundle for non-cookie clients (mobile web / Capacitor shell, M2).

    The cookie login (``/v1/auth/login``) keeps tokens in httpOnly cookies; this is
    its body-returning twin for clients whose origin (``capacitor://`` / a new web
    origin) can't rely on SameSite cookies (认证与会话.md §十). ``expires_in`` is the
    access token's lifetime in seconds so the client refreshes before it lapses;
    ``refresh_expires_in`` is the refresh token's lifetime in seconds (clients that
    persist the refresh token as a cookie need this for ``expirationDate``);
    ``user`` rides the login response (identity in one round trip) and is omitted on
    refresh.
    """

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_expires_in: int | None = None
    user: UserResponse | None = None


class TokenRefreshRequest(BaseModel):
    """Rotate a bearer client's token pair (refresh token in the body, not a cookie)."""

    refresh_token: str = Field(..., min_length=1, max_length=512)


class SessionSummary(BaseModel):
    """One active login device (refresh-token family), owner-scoped."""

    id: str  # token_family
    platform: str | None = None
    user_agent: str | None = None
    ip: str | None = None
    created_at: datetime
    last_used_at: datetime
    current: bool = False


class SessionListResponse(BaseModel):
    data: list[SessionSummary]
    total: int


class TokenRevokeRequest(BaseModel):
    """Bearer-client logout: revoke the presented refresh token's whole family."""

    refresh_token: str = Field(..., min_length=1, max_length=512)

