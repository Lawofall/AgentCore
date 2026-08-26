"""Model catalog (模型目录 · 统一混排) response schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class ModelPriceCard(BaseModel):
    """Reused price card — USD per 1M tokens as decimal strings (money is never float)."""

    cache_hit: str | None = None
    cache_miss: str | None = None
    output: str | None = None


class ModelCatalogCurrent(BaseModel):
    id: str = Field(description="The model id the account currently resolves to.")
    origin: Literal["byok", "platform"] = Field(
        description="Credential origin for the account default model."
    )
    provider_id: str | None = Field(
        default=None,
        description="The BYOK provider the default runs on (null for platform / keyless).",
    )
    ref: str = Field(
        description=(
            "Catalog identity for this row: @platform/{id} or @byok/{provider_id}/{id}."
        ),
    )


class ModelUnavailableReason(BaseModel):
    """Why a listed catalog row cannot be selected. Clients render copy (i18n)."""

    code: Literal["upstream_protocol_unsupported"] = Field(
        description="Structured unavailability code — never a finished user-facing string.",
    )
    required_protocol: Literal["openai_responses", "anthropic_messages"] = Field(
        description=(
            "Upstream protocol this model needs that this gateway does not speak "
            "(chat/completions only)."
        ),
    )


class ModelCatalogItem(BaseModel):
    """One selectable (or greyed-out) model in the user's catalog.

    Product identity is ``ref``. Internally ``(id, origin, provider_id)`` is still unique
    — the same model id may appear under several BYOK providers (and once as a platform
    row), so the picker groups by provider.
    """

    id: str
    ref: str = Field(
        description=(
            "Catalog identity: @platform/{id} or @byok/{provider_id}/{id}. "
            "Copy this into tool `model` fields; do not send origin/provider_id."
        ),
    )
    origin: Literal["byok", "platform"] = Field(
        description="Credential origin when this model is selected (row attribute)."
    )
    display_name: str
    vendor: str
    capabilities: list[str] = Field(
        default_factory=list,
        description="Enabled capability tags — a subset of vision / tools / reasoning.",
    )
    context_length: int | None = Field(
        default=None, description="Context window in tokens (display hint; null if unknown)."
    )
    badge: str | None = Field(
        default=None,
        description=(
            "Curated display badge rendered as-is by clients (e.g. 免费额度). "
            "Null when none — never inferred from model id suffixes."
        ),
    )
    price: ModelPriceCard | None = None
    available: bool = Field(
        default=True,
        description=(
            "Whether the user can switch to this (id, origin, provider_id) now. False = "
            "needs a BYOK key or platform unavailable — the UI greys it and guides to 设置."
        ),
    )
    provider_id: str | None = Field(
        default=None,
        description="For byok rows, the provider this model runs on (null for platform).",
    )
    provider_label: str | None = Field(
        default=None, description="Display name of the provider (byok rows only)."
    )
    unavailable_reason: ModelUnavailableReason | None = Field(
        default=None,
        description=(
            "Present when available=false for a known structured reason. Clients render "
            "copy from code + required_protocol. Null when unspecified or the row is "
            "selectable. Optional for backward compatibility."
        ),
    )


class ModelCatalogResponse(BaseModel):
    """``GET /v1/users/me/models`` — unified model catalog + account default."""

    current: ModelCatalogCurrent
    byok_configured: bool = Field(
        description="Whether the user has at least one BYOK provider configured."
    )
    models: list[ModelCatalogItem]
