"""Conversation share (公开只读分享链接: 对标 ChatGPT 分享) schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CreateShareRequest(BaseModel):
    """Optional expiry when minting a public share link.

    ``expires_in_days=None`` means the link never auto-expires (explicit opt-in).
    Omitted / default ``30`` matches the platform security default.
    """

    expires_in_days: Literal[7, 30] | None = Field(
        default=30,
        description="链接有效天数；null 表示永不过期",
    )


class ShareSummary(BaseModel):
    """One public read-only share (对话或文档).

    ``url`` is a RELATIVE path (``/shared/<id>``) — like ``UserResponse.avatar_url``,
    the client prepends the API origin so the backend stays agnostic of its public
    host. ``id`` is the management handle (used to revoke); it is also the URL token.
    """

    id: str
    url: str
    title: str
    created_at: datetime
    expires_at: datetime | None = None


class ShareListResponse(BaseModel):
    data: list[ShareSummary]
    total: int
