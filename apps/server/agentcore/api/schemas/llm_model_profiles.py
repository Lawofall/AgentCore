"""Model combination profile (模型组合) API schemas.

Distinct from scenario ``ProfileParams`` (temperature / rounds) — this is the
account/session selectable ``{main, worker?}`` combination.
"""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class ModelProfileSlot(BaseModel):
    """One slot in a model combination (main / worker)."""

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
    recipe: Literal["chat", "full", "standard"] | None = Field(
        default=None,
        description=(
            "Official recipe still locked on this assembly. "
            "Null on user snapshots. Official 标准 / 完整 / 极简 themselves are virtual ids."
        ),
    )
    main: ModelProfileSlot | None = None
    worker: ModelProfileSlot | None = None
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
    omitted_factory_skills: list[str] = Field(
        default_factory=list,
        description=(
            "Official HOW names this assembly leaves off the consult directory. "
            "Empty = every factory row stays. A name on the list is missing from "
            "the directory and consult. New skills default on once the recipe "
            "is released."
        ),
    )
    disabled_tools: list[str] = Field(
        default_factory=list,
        description="Whole-tool switch ids this assembly turns off. Empty = all on.",
    )
    omitted_projections: list[str] = Field(
        default_factory=list,
        description="Envelope rows this assembly does not write. Empty = every row.",
    )
    omit_desk_rules: bool | None = Field(
        default=None,
        description=(
            "True when this turn does not read folder AgentCore/rules. "
            "Null/omitted = false."
        ),
    )
    skill_membership: dict[str, str] | None = Field(
        default=None,
        description=(
            "Account 交代 document id → apply mode. Null = follow the documents "
            "(official recipes). Empty object = this assembly names none."
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
    """New user assembly. Always a snapshot of official 标准, not a locked recipe."""

    name: str = Field(max_length=200)
    set_as_default: bool = False
    origin: Literal["standard"] = Field(
        default="standard",
        description=(
            "Snapshot of official 标准 (daily tools on; debate / chats / folders off)."
        ),
    )


class UpdateLlmModelProfileRequest(BaseModel):
    """Partial update. Explicit null on worker clears that slot.

    Changing the model does not release a locked recipe.
    """

    name: str | None = Field(default=None, max_length=200)
    main: ModelProfileSlot | None = None
    worker: ModelProfileSlot | None = None
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
    omitted_factory_skills: list[str] | None = Field(
        default=None,
        description=(
            "Official HOW names to leave off this assembly. "
            "Omitted = unchanged. Empty list = every factory row stays."
        ),
    )


class SetDefaultModelProfileRequest(BaseModel):
    profile_id: str = Field(description="User combination id or system preset id")
