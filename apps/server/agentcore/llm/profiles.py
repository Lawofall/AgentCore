"""Scenario profiles: static inference params + request assembly.

Model × params **selection** strategy lives in :mod:`agentcore.llm.model_selection`.
This module keeps the ``PROFILES`` table, ``TurnProfiles`` carrier, and
:func:`build_request` packing only — no purpose→model or turn-assembly decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentcore.llm.credentials import LLMCredentials

from agentcore.llm.provider.protocol import LLMMessage, LLMRequest

# Platform / catalog model id constants.
# Official V4.1 Flash (BYOK DeepSeek API). Distinct from the OpenCode Go wire id.
DEEPSEEK_V41_FLASH = "deepseek-flash"
# OpenCode Go V4.1 Flash — current platform pin. Not the official ``deepseek-flash``
# id (Go/Zen hideFromPicker omits it; vision contract differs).
OPENCODE_GO_V41_FLASH = "deepseek-v4.1-flash"
PLATFORM_MODEL_FLASH = OPENCODE_GO_V41_FLASH
# Retired V4 Flash / Pro: leftover chats, eval aliases, hideFromPicker.
# Flash alias still meters at official V4.1 CNY (DeepSeek still routes the old
# name). Pro has no product card.
DEEPSEEK_V4_FLASH = "deepseek-v4-flash"
PLATFORM_MODEL_PRO = "deepseek-v4-pro"
DEEPSEEK_V4_PRO = PLATFORM_MODEL_PRO
# OpenCode Zen free SKU (upstream ¥0); product still meters at official Flash CNY.
DEEPSEEK_V4_FLASH_FREE = "deepseek-v4-flash-free"

# Router / ``agent_provider_id`` sentinel when a worker override runs on platform credentials
# (main turn may be BYOK). ``route_model_for("agent")`` prefixes ``platform/{model}``;
# ``build_turn_router`` / debate extras register :func:`build_platform_provider` under
# this key (per-model credentials via ``platform_llm_credentials(model=…)``).
PLATFORM_PROVIDER_SENTINEL = "platform"


@dataclass(frozen=True)
class ProfileParams:
    """Inference params for one usage scenario (no model — use ModelConfig.model)."""

    temperature: float = 0.7
    max_tokens: int | None = None
    # 0 = no product round fuse (loop exits on end_turn / token / wall / spin /
    # circuit / Stop). Positive = explicit cap (tests, CEO-stamped short, light_repair).
    max_rounds: int = 0
    name: str = ""
    # True = send thinking.type=enabled. False = force off for background
    # one-shots (title / compaction / …) so a tight max_tokens budget is not eaten
    # by reasoning_content (平台LLM接入 · DeepSeek 易错). None = no profile
    # opinion; the wire still sends enabled for thinking_type_switch models
    # (do not rely on omit=on — OpenCode Go treats omit as off).
    thinking: bool | None = None
    # Combination-level vendor effort token overlaid by TurnProfiles.get().
    reasoning_effort: str | None = None


PROFILES: dict[str, ProfileParams] = {
    "chat": ProfileParams(temperature=0.7, max_rounds=0, thinking=True),
    # Single delegated-worker profile: no product round fuse (0). 力度差异由
    # 委派协作结构（拆分 / 复审 / replan）表达；防失控靠 token / 墙钟 / spin / 熔断 / Stop。
    "agent": ProfileParams(temperature=0.7, max_rounds=0, thinking=True),
    "compaction": ProfileParams(temperature=0.3, max_rounds=1, thinking=False),
    "file.rewrite": ProfileParams(temperature=0.4, max_rounds=1, thinking=False),
    "title": ProfileParams(temperature=0.3, max_tokens=1024, max_rounds=1, thinking=False),
}

_DEFAULT_PROFILE = "chat"


def get_profile(name: str) -> ProfileParams:
    resolved = name if name in PROFILES else _DEFAULT_PROFILE
    return replace(PROFILES[resolved], name=resolved)


def agent_profile() -> ProfileParams:
    """The single delegated-worker profile (unified round budget, no tiers)."""
    return get_profile("agent")


def build_request(
    profile: ProfileParams,
    messages: list[LLMMessage],
    *,
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
    stream: bool = True,
    model: str,
) -> LLMRequest:
    return LLMRequest(
        messages=messages,
        model=model,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        tools=tools,
        tool_choice=tool_choice if tools else "none",
        stream=stream,
        scenario=profile.name or _DEFAULT_PROFILE,
        thinking=profile.thinking,
        reasoning_effort=profile.reasoning_effort,
    )


@dataclass(frozen=True)
class TurnProfiles:
    """Turn-level resolved model + static scenario params (replaces ProfileSet)."""

    model: str
    model_overrides: dict[str, str] = field(default_factory=dict)
    # BYOK provider id or ``PLATFORM_PROVIDER_SENTINEL`` for platform-credential worker
    # overrides; None = follow turn creds. Cross-origin / cross-provider worker defaults
    # register on the turn ProviderRouter so ``route_model_for("agent")`` can dispatch
    # with a ``provider_id/model`` (or ``platform/model``) prefix.
    agent_provider_id: str | None = None
    # Combination-level vendor thinking-effort token. None = that model's default.
    reasoning_effort: str | None = None
    # Shorter context ceiling from the assembly. None = each role's catalog window.
    context_budget: int | None = None

    def model_for(self, profile_name: str) -> str:
        return self.model_overrides.get(profile_name, self.model)

    def route_model_for(
        self, profile_name: str, *, turn_provider_id: str | None = None
    ) -> str:
        """Model id for an LLMRequest — may include a router prefix for cross-provider agent."""
        model = self.model_for(profile_name)
        if (
            profile_name == "agent"
            and self.agent_provider_id
            and self.agent_provider_id != turn_provider_id
        ):
            return f"{self.agent_provider_id}/{model}"
        return model

    def get(self, name: str) -> ProfileParams:
        resolved = get_profile(name)
        if resolved.thinking is False or self.reasoning_effort is None:
            return resolved
        return replace(resolved, reasoning_effort=self.reasoning_effort)

    def agent(self) -> ProfileParams:
        return self.get("agent")


def default_turn_profiles(*, model: str | None = None) -> TurnProfiles:
    from agentcore.config import settings

    return TurnProfiles(model=model or settings.platform_model)


def turn_profiles_for_turn(
    profile_set: TurnProfiles | None = None,
    llm_credentials: LLMCredentials | None = None,
) -> TurnProfiles:
    """Thin re-export — strategy lives in :func:`model_selection.turn_profiles_for_turn`."""
    from agentcore.llm.model_selection import turn_profiles_for_turn as _select

    return _select(profile_set, llm_credentials)

