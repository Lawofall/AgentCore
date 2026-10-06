"""Model combination profile (模型组合) API schemas.

Distinct from scenario ``ProfileParams`` (temperature / rounds) — this is the
account/session selectable ``{main, worker?, background?, vision?}`` combination.
"""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class ModelProfileSlot(BaseModel):
    """One slot in a model combination (main / worker / background / vision)."""

    origin: Literal["byok", "platform"] = Field(
        description="Credential origin: byok (user key) or platform (operator catalog)"
    )
    provider_id: str | None = Field(
        default=None,
        description="BYOK provider id when origin=byok; must be null for platform",
    )
    model: str = Field(max_length=200)

    @model_validator(mode="after")
    def _origin_provider_consistency(self) -> Self:
        if self.origin == "platform":
            if self.provider_id:
                raise ValueError("platform 指针不能带 provider_id")
            return self
        if not (self.provider_id or "").strip():
            raise ValueError("byok 指针必须指定 provider_id")
        return self


class LlmModelProfileView(BaseModel):
    """One assembly, including its model columns."""

    id: str
    name: str
    kind: Literal["system", "user", "implicit"]
    is_default: bool = False
    recipe: Literal["chat", "web", "full"] | None = Field(
        default=None,
        description=(
            "Official recipe still locked on this assembly. "
            "Null once tools, the envelope, the factory catalog, or plugs "
            "are edited, or the row was never a recipe."
        ),
    )
    main: ModelProfileSlot | None = None
    worker: ModelProfileSlot | None = None
    background: ModelProfileSlot | None = None
    vision: ModelProfileSlot | None = None
    reasoning_effort: str | None = None
    context_budget: int | None = Field(
        default=None,
        description=(
            "Shorter context ceiling in tokens (128000 / 256000 / 512000). "
            "Null = the main model's own window."
        ),
    )
    enabled_mcp_server_ids: list[str] = Field(
        default_factory=list,
        description="Local MCP server ids this assembly enables. Empty = none.",
    )
    omit_factory_catalog: bool | None = Field(
        default=None,
        description=(
            "When true, this assembly does not carry the three factory skill rows. "
            "consult stays only if user on-demand rows remain. Null means the "
            "factory rows are still on."
        ),
    )
    created_at: datetime | None = None
    updated_at: datetime | None = None
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Ignorable BYOK model reachability hints from the last save "
            "(empty on list/get). Save still succeeds when non-empty."
        ),
    )

class LlmModelProfileListResponse(BaseModel):
    data: list[LlmModelProfileView]
    default_assembly_id: str | None = None


class CreateLlmModelProfileRequest(BaseModel):
    """New assembly. Copies the starred assembly, including its model."""

    name: str = Field(max_length=200)
    set_as_default: bool = False


class UpdateLlmModelProfileRequest(BaseModel):
    """Partial update. Explicit null on worker/background/vision clears that slot.

    Changing the model does not release a locked recipe.
    """

    name: str | None = Field(default=None, max_length=200)
    main: ModelProfileSlot | None = None
    worker: ModelProfileSlot | None = None
    background: ModelProfileSlot | None = None
    vision: ModelProfileSlot | None = None
    reasoning_effort: str | None = Field(default=None, max_length=32)
    context_budget: int | None = Field(
        default=None,
        description=(
            "Shorter context ceiling in tokens. Null = the main model's own window. "
            "Omitted = unchanged."
        ),
    )
    enabled_mcp_server_ids: list[str] | None = Field(
        default=None,
        description=(
            "Local MCP server ids this assembly enables. "
            "Omitted = unchanged. Empty list = this assembly enables none."
        ),
    )
    omit_factory_catalog: bool | None = Field(
        default=None,
        description=(
            "Omit the three factory skill rows on this assembly. "
            "Omitted = unchanged."
        ),
    )


class SetDefaultModelProfileRequest(BaseModel):
    profile_id: str = Field(description="User combination id or system preset id")
