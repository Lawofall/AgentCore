"""BYOK LLM provider configuration (多服务商列表, llm/provider_service.py) schemas.

A user configures a LIST of OpenAI-compatible providers (each: label + key + endpoint).
Account / conversation model selection uses **model combination profiles**
(see ``llm_model_profiles`` schemas) — not a model id on the provider itself.
"""

from datetime import datetime

from pydantic import BaseModel, Field

# Stable OpenAPI example — do NOT use settings.platform_base_url (env-dependent → CI drift).
_OPENAPI_BASE_URL_EXAMPLE = "https://api.deepseek.com"


class CreateLlmProviderRequest(BaseModel):
    """Add one OpenAI-compatible BYOK provider to the account's list."""

    label: str = Field(
        default="",
        max_length=100,
        description="Display name for this provider (e.g. DeepSeek, 火山方舟)",
    )
    api_key: str = Field(
        ...,
        max_length=400,
        description="Plaintext API key (stored AES-256-GCM encrypted; never returned).",
    )
    base_url: str | None = Field(
        default=None,
        max_length=500,
        description="OpenAI-compatible endpoint including version prefix",
        examples=[_OPENAPI_BASE_URL_EXAMPLE],
    )


class UpdateLlmProviderRequest(BaseModel):
    """Partial update of a provider. Only fields present in the body are applied; an
    omitted ``api_key`` keeps the stored ciphertext (edit endpoint without
    re-entering the key)."""

    label: str | None = Field(default=None, max_length=100)
    api_key: str | None = Field(default=None, max_length=400)
    base_url: str | None = Field(default=None, max_length=500)


class LlmProviderView(BaseModel):
    """Settings view of one BYOK provider — never the plaintext key."""

    id: str
    label: str
    base_url: str
    status: str = Field(description="Connectivity result: unchecked | active | error")
    masked_key: str | None = None
    supports_tools: bool | None = None
    # Transient message from the connectivity test (POST .../test) only.
    message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LlmProvidersResponse(BaseModel):
    """The full 设置·模型配置 state: provider list + deployment caps.

    The starred assembly lives on ``/users/me/assemblies``
    (``default_assembly_id``). Its model columns are the account default brain.
    """

    providers: list[LlmProviderView]
    default_assembly_id: str | None = None
    billing_mode: str = Field(
        default="byok",
        description=(
            "Deployment billing mode (config.billing_mode). In 'platform' a keyless "
            "user runs on platform credit and BYOK is opt-in; in 'byok' a provider is "
            "required (402 if missing)."
        ),
    )
    platform_available: bool = Field(
        default=False,
        description=(
            "Whether platform-billed models are usable on this deployment "
            "(billing selectable ∧ platform credentials). False while BYOK-dormant "
            "even if PLATFORM_API_KEY is still configured."
        ),
    )
    platform_model: str | None = Field(
        default=None, description="Operator platform model id when platform is available"
    )
