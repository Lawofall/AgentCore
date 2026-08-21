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

# Platform model id constants (eval / pricing / migration defaults).
PLATFORM_MODEL_FLASH = "deepseek-v4-flash"
PLATFORM_MODEL_PRO = "deepseek-v4-pro"
DEEPSEEK_V4_FLASH = PLATFORM_MODEL_FLASH
DEEPSEEK_V4_PRO = PLATFORM_MODEL_PRO
# OpenCode Zen free SKU (upstream ¥0); product still meters at Flash nominal via pricing.
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
    max_rounds: int = 16
    name: str = ""
    # True = send thinking.type=enabled. False = force off for background
    # one-shots (title / memory / …) so a tight max_tokens budget is not eaten
    # by reasoning_content (平台LLM接入 · DeepSeek 易错). None = no profile
    # opinion; the wire still sends enabled for thinking_type_switch models
    # (do not rely on omit=on — OpenCode Go treats omit as off).
    thinking: bool | None = None


PROFILES: dict[str, ProfileParams] = {
    "chat": ProfileParams(temperature=0.7, max_rounds=16, thinking=True),
    # Single delegated-worker profile: one round fuse (80) for every worker —
    # 力度差异由委派协作结构（拆分 / 复审 / replan）表达，不再有 per-worker 档位。
    # Keep in sync with ``MAX_TASK_ROUNDS`` (runs/constants.py).
    "agent": ProfileParams(temperature=0.7, max_rounds=80, thinking=True),
    "memory": ProfileParams(temperature=0.3, max_rounds=1, thinking=False),
    "compaction": ProfileParams(temperature=0.3, max_rounds=1, thinking=False),
    "file.rewrite": ProfileParams(temperature=0.4, max_rounds=1, thinking=False),
    "title": ProfileParams(temperature=0.3, max_tokens=1024, max_rounds=1, thinking=False),
    # 固化工作流时抽槽位：结构化 JSON 一次性抽取，低温 + 不思考（同 title / memory）。
    "workflow.slots": ProfileParams(
        temperature=0.2, max_tokens=1024, max_rounds=1, thinking=False
    ),
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
        return get_profile(name)

    def agent(self) -> ProfileParams:
        return agent_profile()


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

