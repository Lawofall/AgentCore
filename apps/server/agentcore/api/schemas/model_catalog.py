"""Model catalog (模型目录 · 统一混排) response schemas."""

from typing import Literal

from pydantic import BaseModel, Field

from agentcore.api.schemas.conversations import ConversationModelOverride


class ComposerModelKnobs(BaseModel):
    """Last effort / context for one catalog ref."""

    reasoning_effort: str | None = None
    context_budget: int | None = Field(
        default=None,
        description="Shorter context ceiling in tokens. Null = that model's own window.",
    )


class ModelPriceCard(BaseModel):
    """Curated unit prices per 1M tokens as decimal strings (money is never float)."""

    cache_hit: str | None = None
    cache_miss: str | None = None
    output: str | None = None
    currency: Literal["CNY"] = Field(
        default="CNY",
        description="ISO-4217. Catalog cards are curated selling CNY (1 积分 = ¥1 卖价).",
    )
    multiplier: str = Field(
        default="1",
        description=(
            "Sell rate vs that model's official CNY card, decimal string. "
            "Platform Flash is 0.25; Claude 0.3; GPT-6 0.18; 1 = official. "
            "Picker row shows this. BYOK rows stay 1."
        ),
    )
    quota_multiplier: str | None = Field(
        default=None,
        description=(
            "Credit-spend rate vs platform Flash off-peak sell "
            "(1M input + 1M output), integer string. 1 = same 积分 as Flash. "
            "Null on BYOK / unpriced. Official-vs-vendor sell rate stays in "
            "multiplier. Picker card shows this."
        ),
    )


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
            "Protocol this catalog row needs that is unavailable on this origin. "
            "Platform greys unpriced off-protocol ids; priced Claude / GPT stay "
            "selectable. BYOK OpenCode already speaks both protocols."
        ),
    )


class ModelReasoningEffort(BaseModel):
    """Vendor Chat Completions ``reasoning_effort`` control surface."""

    options: list[str] = Field(
        description="Official control tokens in vendor order. Aliases are not listed."
    )
    default: str = Field(
        description="Vendor default when a combination leaves reasoning_effort unset."
    )


class ModelCatalogItem(BaseModel):
    """One selectable (or greyed-out) model in the user's catalog.

    Product identity is ``ref``. Internally ``(id, origin, provider_id)`` is still unique
    — the same model id may appear under several BYOK providers (and once as a platform
    row). Composer pickers are a flat list; the settings page groups by channel.
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
            "Display badge rendered as-is by clients. Curated SKU marks "
            "(e.g. 免费额度) only. Platform origin is unmarked. "
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
    reasoning_effort: ModelReasoningEffort | None = Field(
        default=None,
        description=(
            "Official vendor thinking-effort tokens for this model id "
            "(e.g. low/high/max). Null when the leaf does not send reasoning_effort."
        ),
    )
    custom: bool = Field(
        default=False,
        description=(
            "True when this BYOK row was opened by hand-fill (enabled_refs) "
            "rather than vendor seed or GET /models. Clients may badge it 自定义. "
            "Inference ignores this flag."
        ),
    )


class ModelCatalogResponse(BaseModel):
    """``GET /v1/users/me/models`` — unified model catalog + account default."""

    current: ModelCatalogCurrent
    byok_configured: bool = Field(
        description="Whether the user has at least one BYOK provider configured."
    )
    models: list[ModelCatalogItem]
    hidden_refs: list[str] = Field(
        default_factory=list,
        description=(
            "Catalog refs this account hid from composer pickers. "
            "Platform rows default on; a ref here stays off the picker. "
            "The models list is the full catalog; inference ignores this set."
        ),
    )
    enabled_refs: list[str] = Field(
        default_factory=list,
        description=(
            "BYOK catalog refs this account opened onto composer pickers. "
            "Connecting a provider does not add its directory here; "
            "Settings · 模型 is the inventory. Inference ignores this set."
        ),
    )
    composer_model: ConversationModelOverride | None = Field(
        default=None,
        description=(
            "Account last-used composer snapshot. New chats start from this; "
            "null = follow the starred assembly until the picker writes one."
        ),
    )
    composer_model_knobs: dict[str, ComposerModelKnobs] = Field(
        default_factory=dict,
        description=(
            "Last effort / context per catalog ref "
            "(@platform/{id} or @byok/{provider_id}/{id}). "
            "Switching a model restores that leaf; missing key = vendor default / window."
        ),
    )


class HiddenModelRefsUpdate(BaseModel):
    """``PUT /v1/users/me/models/hidden`` — replace picker membership."""

    refs: list[str] = Field(
        default_factory=list,
        description="Refs kept off pickers (platform opt-out; leftover BYOK denylist).",
    )
    enabled_refs: list[str] | None = Field(
        default=None,
        description=(
            "BYOK refs on pickers. Null leaves the stored allowlist unchanged "
            "(older clients that only send refs)."
        ),
    )


class HiddenModelRefsView(BaseModel):
    refs: list[str]
    enabled_refs: list[str] = Field(default_factory=list)


class ModelDisplayNameUpdate(BaseModel):
    """``PUT /v1/users/me/models/display-names`` — one BYOK catalog alias."""

    ref: str = Field(description="Catalog identity ``@byok/{provider_id}/{id}``.")
    display_name: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Shown name for this connected model. Null or blank reverts to "
            "the catalog brand. Platform refs are rejected."
        ),
    )


class ModelDisplayNameView(BaseModel):
    ref: str
    display_name: str | None = None


class VisionAcceptUpdate(BaseModel):
    """``PUT /v1/users/me/models/vision-accept`` — one BYOK catalog declare."""

    ref: str = Field(description="Catalog identity ``@byok/{provider_id}/{id}``.")
    accept: bool = Field(
        description=(
            "True = this connected model accepts native image parts. "
            "False clears the declare. Platform refs are rejected."
        ),
    )


class VisionAcceptView(BaseModel):
    ref: str
    accept: bool
