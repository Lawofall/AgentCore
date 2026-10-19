"""Account remote MCP Server REST schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class CreateMcpServerRequest(BaseModel):
    name: str = Field(default="", max_length=100)
    url: str = Field(..., min_length=8, max_length=2000)
    bearer: str | None = Field(
        default=None,
        max_length=4000,
        description="Optional static bearer token (AES-GCM at rest; never returned).",
    )


class UpdateMcpServerRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, min_length=8, max_length=2000)
    bearer: str | None = Field(default=None, max_length=4000)


class McpServerView(BaseModel):
    id: str
    name: str
    url: str
    auth_kind: str
    status: str
    has_token: bool
    last_error: str
    created_at: datetime
    updated_at: datetime


class McpServerListResponse(BaseModel):
    data: list[McpServerView]
    total: int


class McpServerTestResponse(BaseModel):
    id: str
    status: str
    tools: list[str]
    last_error: str


class McpOAuthStartRequest(BaseModel):
    return_origin: str = Field(default="", max_length=500)


class McpOAuthStartResponse(BaseModel):
    authorization_url: str
