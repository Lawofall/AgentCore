"""Single strategy entry: model × scenario request-params.

Orchestrates (does not own) catalog 上架 facts, resolve credentials, and
model_profiles expand — then pairs the chosen model with scenario
:class:`~agentcore.llm.profiles.ProfileParams`.

**Owns (strategy):** purpose→model priority, turn profile assembly, pairing
model with inference params, and the thin ``build_selected_request`` path into
:func:`agentcore.llm.profiles.build_request`.

**Does not own:** credential decrypt / platform key wiring (:mod:`resolve`),
catalog listing / enrichment (:mod:`catalog`), combo CRUD/expand
(:mod:`model_profiles`), or raw ``LLMRequest`` field packing
(:func:`profiles.build_request`).

P2-A — call sites that need 「which model + which params」 should enter here
(or via thin wrappers that delegate here). Do not reintroduce a second metadata
source alongside catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.config import settings
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.profiles import (
    PLATFORM_MODEL_FLASH,
    PLATFORM_PROVIDER_SENTINEL,
    ProfileParams,
    TurnProfiles,
    build_request,
    default_turn_profiles,
    get_profile,
)
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest
from agentcore.llm.resolve import ModelConfig, ModelOrigin

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_BACKGROUND_PURPOSES = frozenset(
    {"title", "memory", "compaction", "workflow.slots"}
)

__all__ = [
    "SelectedCall",
    "build_selected_request",
    "select_call",
    "select_for_scenario",
    "select_model_config",
    "select_turn_model",
    "select_turn_profiles",
    "select_user_chat_model",
    "turn_profiles_for_turn",
]


@dataclass(frozen=True)
class SelectedCall:
    """Strategy output: model id × scenario inference params."""

    model: str
    profile: ProfileParams
    origin: ModelOrigin | None = None
    provider_id: str | None = None


def select_call(
    scenario: str,
    model: str,
    *,
    origin: ModelOrigin | None = None,
    provider_id: str | None = None,
) -> SelectedCall:
    """Pair an already-resolved model id with scenario ProfileParams."""
    return SelectedCall(
        model=model,
        profile=get_profile(scenario),
        origin=origin,
        provider_id=provider_id,
    )


def select_for_scenario(
    turn: TurnProfiles,
    scenario: str,
    *,
    turn_provider_id: str | None = None,
) -> SelectedCall:
    """Pair turn-resolved model with scenario ProfileParams."""
    return SelectedCall(
        model=turn.route_model_for(scenario, turn_provider_id=turn_provider_id),
        profile=turn.get(scenario),
    )


def build_selected_request(
    selected: SelectedCall,
    messages: list[LLMMessage],
    *,
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
    stream: bool = True,
) -> LLMRequest:
    """Assemble ``LLMRequest`` from a :class:`SelectedCall` (no further strategy)."""
    return build_request(
        selected.profile,
        messages,
        tools=tools,
        tool_choice=tool_choice,
        stream=stream,
        model=selected.model,
    )


def select_turn_model(
    credentials: LLMCredentials | None,
    *,
    conversation_model: str | None = None,
) -> str:
    """Resolve the model for a user-facing turn.

    Priority: explicit ``conversation_model`` (already expanded main from the profile)
    > account ``default_model`` (BYOK creds) > ``platform_model`` > Flash.
    """
    if conversation_model and conversation_model.strip():
        return conversation_model.strip()
    if credentials is not None and credentials.default_model:
        return credentials.default_model
    if settings.platform_model:
        return settings.platform_model
    return PLATFORM_MODEL_FLASH


def turn_profiles_for_turn(
    profile_set: TurnProfiles | None = None,
    llm_credentials: LLMCredentials | None = None,
) -> TurnProfiles:
    """Resolve turn profiles for a pipeline/sidecar run.

    BYOK and inference-proxy turns must not inherit ``settings.platform_model`` when
    the caller did not supply an explicit profile set — the upstream model comes from
    the user's credentials (direct BYOK) or from the proxy's server-side resolution.
    """
    if profile_set is not None:
        return profile_set
    if llm_credentials is not None:
        return default_turn_profiles(model=select_turn_model(llm_credentials))
    return default_turn_profiles()


async def select_turn_profiles(
    session: AsyncSession,
    conv: Any,
    user_id: str,
    credentials: LLMCredentials | None = None,
) -> TurnProfiles:
    """Resolve model + static profiles for this turn.

    Expands the conversation's model combination profile (or account default) into
    main + optional worker override. Empty worker slot → workers follow main.
    Cross-origin / cross-provider worker credentials land in ``agent_provider_id``.
    """
    from agentcore.llm.resolve import (
        resolve_account_worker_selection,
        resolve_conversation_model_selection,
        resolve_credentials,
    )

    if credentials is None:
        credentials = await resolve_credentials(session, user_id, "user_facing")
    selection = await resolve_conversation_model_selection(session, conv, user_id)
    overrides: dict[str, str] = {}
    agent_provider_id: str | None = None
    worker = await resolve_account_worker_selection(session, user_id, conv=conv)
    if worker is not None and (
        worker.model != selection.model
        or worker.origin != selection.origin
        or worker.provider_id != selection.provider_id
    ):
        overrides["agent"] = worker.model
        if worker.origin != selection.origin or worker.provider_id != selection.provider_id:
            agent_provider_id = (
                PLATFORM_PROVIDER_SENTINEL
                if worker.origin == "platform"
                else worker.provider_id
            )
    return TurnProfiles(
        model=selection.model,
        model_overrides=overrides,
        agent_provider_id=agent_provider_id,
    )


def _model_for_purpose(
    purpose: str,
    *,
    chat_model: str,
    user_background_model: str | None = None,
) -> str:
    """Resolve model name for ``purpose``; background prefers background_model."""
    if purpose not in _BACKGROUND_PURPOSES:
        return chat_model
    if user_background_model and user_background_model.strip():
        return user_background_model.strip()
    platform_bg = (settings.platform_background_model or "").strip()
    if platform_bg:
        return platform_bg
    return chat_model


async def select_model_config(
    session: AsyncSession,
    user_id: str,
    purpose: str = "chat",
) -> ModelConfig | None:
    """Resolve full upstream config for one LLM purpose (selection / advisory).

    SELECTION / ADVISORY ONLY — never an authorization path (01 F10). For a keyless
    user this deliberately FALLS BACK to the platform model so token advisory / turn-
    profile selection still resolves a NAME; the billing gate
    (``preflight_llm_credentials`` / ``resolve_and_gate_background`` /
    ``run_background_llm``) is the authorization choke point.

Background purposes (title/memory/workflow.slots) are **platform-first**
    product chrome when :func:`platform_catalog_visible` — *unless* the user pointed
    the combo's background slot at their own key, which outranks the platform default:
    「平台优先」 exists to stop BYOK accounts freeloading platform quota, and an account
    spending its own key has nothing to freeload. Otherwise BYOK is the fallback when
    the platform gate is off **or** upstream auth rejection via ``run_background_llm``.
    Compaction is continuity, not chrome: do **not** resolve fold credentials here;
    ``run_compaction_llm`` follows the conversation's chat payer. Chat purpose stays
    user-key-first unless the account default is an explicit platform pointer.

    Credential decrypt / platform key wiring stay in :mod:`resolve`; this function
    only decides *which* binding to use.
    """
    from agentcore.billing.preference import platform_catalog_visible
    from agentcore.llm.resolve import (
        _account_default,
        _decrypt_provider,
        _model_config_from_creds,
        platform_llm_credentials,
        resolve_background_user_fallback,
        resolve_explicit_background_byok,
    )

    is_background = purpose in _BACKGROUND_PURPOSES

    if is_background:
        if platform_catalog_visible():
            explicit = await resolve_explicit_background_byok(session, user_id, purpose)
            if explicit is not None:
                return explicit
            platform_model = _model_for_purpose(
                purpose, chat_model=settings.platform_model
            )
            platform = platform_llm_credentials(model=platform_model)
            if platform is not None:
                return ModelConfig(
                    model=platform_model,
                    base_url=platform.base_url,
                    api_key=platform.api_key,
                    source="platform",
                    purpose=purpose,
                )
        return await resolve_background_user_fallback(session, user_id, purpose)

    row, chat_model, origin = await _account_default(session, user_id)
    if origin == "byok" and row is not None:
        creds = _decrypt_provider(row, user_id)
        if creds is not None:
            return _model_config_from_creds(creds, chat_model, purpose)

    platform_model = (
        chat_model
        if origin == "platform"
        else _model_for_purpose(purpose, chat_model=settings.platform_model)
    )
    if platform_catalog_visible():
        platform = platform_llm_credentials(model=platform_model)
        if platform is not None:
            return ModelConfig(
                model=platform_model,
                base_url=platform.base_url,
                api_key=platform.api_key,
                source="platform",
                purpose=purpose,
            )
    return None


async def select_user_chat_model(session: AsyncSession, user_id: str) -> str:
    """Chat model for a user-facing turn — matches inference proxy upstream resolution."""
    from agentcore.llm.resolve import platform_llm_credentials, resolve_model_config

    # Prefer the resolve facade so call-site / test monkeypatches on
    # ``resolve.resolve_model_config`` still apply.
    cfg = await resolve_model_config(session, user_id, "chat")
    if cfg is not None:
        return cfg.model
    platform = platform_llm_credentials()
    if platform is not None:
        return settings.platform_model
    return PLATFORM_MODEL_FLASH
