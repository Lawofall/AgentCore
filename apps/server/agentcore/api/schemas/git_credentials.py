"""Account-level Git credentials schemas (G3 · 云私仓 PAT)."""

from datetime import datetime

from pydantic import BaseModel, Field


class UpsertGitCredentialRequest(BaseModel):
    """Save / replace the account Git PAT (encrypted at rest; never returned)."""

    token: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Plaintext PAT / OAuth token (AES-256-GCM at rest; never returned).",
    )


class GitCredentialView(BaseModel):
    """Settings view of the account Git credential — never the plaintext token."""

    configured: bool
    masked_token: str | None = None
    updated_at: datetime | None = None
