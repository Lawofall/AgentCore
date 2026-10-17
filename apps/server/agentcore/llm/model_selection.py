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
from agentcore.llm.resolve import ModelConfig, ModelOrigin, ModelSelection

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_BACKGROUND_PURPOSES = frozenset({"title", "compaction"})

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
    from agentcore.llm.model_profiles import LlmModelProfileService
    from agentcore.llm.resolve import resolve_credentials

    if credentials is None:
        credentials = await resolve_credentials(session, user_id, "user_facing")
    expanded = await LlmModelProfileService(session).expand_for_conversation(
        user_id, conv
    )
    selection = expanded.main
    overrides: dict[str, str] = {}
    agent_provider_id: str | None = None
    worker = expanded.worker
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
        reasoning_effort=expanded.reasoning_effort,
        context_budget=expanded.context_budget,
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


async def _expand_title_profile(
    session: AsyncSession,
    user_id: str,
    conversation_id: str | None,
):
    """Assembly whose background/main slots title should read.

    A conversation id uses that conversation's pin. Otherwise the account star.
    """
    from agentcore.llm.model_profiles import LlmModelProfileService

    return await LlmModelProfileService(session).expand_for_conversation_id(
        user_id, conversation_id
    )


def _platform_model_config(model: str, purpose: str) -> ModelConfig | None:
    from agentcore.llm.resolve import platform_llm_credentials

    platform = platform_llm_credentials(model=model)
    if platform is None:
        return None
    return ModelConfig(
        model=model,
        base_url=platform.base_url,
        api_key=platform.api_key,
        source="platform",
        purpose=purpose,
    )


async def _byok_slot_config(
    session: AsyncSession,
    user_id: str,
    selection: ModelSelection,
    purpose: str,
) -> ModelConfig | None:
    from agentcore.llm.resolve import (
        _decrypt_provider,
        _load_provider,
        _model_config_from_creds,
    )

    if selection.origin != "byok" or not selection.provider_id:
        return None
    row = await _load_provider(session, user_id, selection.provider_id)
    if row is None:
        return None
    creds = _decrypt_provider(row, user_id)
    if creds is None:
        return None
    return _model_config_from_creds(creds, selection.model, purpose)


async def _title_config_when_catalog_open(
    session: AsyncSession,
    user_id: str,
    *,
    conversation_id: str | None,
) -> ModelConfig | None:
    """Title binding while the platform catalog is visible.

    Explicit BYOK background slot wins. Explicit platform slot stays on the
    platform chrome model. An empty slot follows this assembly's main model
    and that model's key. ``None`` means the caller should use the BYOK fallback.
    """
    expanded = await _expand_title_profile(session, user_id, conversation_id)
    bg = expanded.background
    main = expanded.main
    if bg is not None and bg.origin == "byok":
        explicit = await _byok_slot_config(session, user_id, bg, "title")
        if explicit is not None:
            return explicit
        chrome = _model_for_purpose("title", chat_model=settings.platform_model)
        return _platform_model_config(chrome, "title")
    if bg is not None and bg.origin == "platform":
        chrome = _model_for_purpose("title", chat_model=settings.platform_model)
        return _platform_model_config(chrome, "title")
    if main.origin == "byok":
        return await _byok_slot_config(session, user_id, main, "title")
    return _platform_model_config(main.model, "title")


async def select_model_config(
    session: AsyncSession,
    user_id: str,
    purpose: str = "chat",
    *,
    conversation_id: str | None = None,
) -> ModelConfig | None:
    """Resolve full upstream config for one LLM purpose (selection / advisory).

    SELECTION / ADVISORY ONLY — never an authorization path (01 F10). For a keyless
    user this deliberately FALLS BACK to the platform model so token advisory / turn-
    profile selection still resolves a NAME; the billing gate
    (``preflight_llm_credentials`` / ``resolve_and_gate_background`` /
    ``run_background_llm``) is the authorization choke point.

    Title reads the conversation's assembly when ``conversation_id`` is set, else
    the account star. An explicit BYOK background slot outranks everything. An
    empty slot follows that assembly's main model and key. An explicit platform
    slot stays on the platform chrome model (``PLATFORM_BACKGROUND_MODEL`` or the
    platform model) and still passes ``enforce_quota`` at the gate. Compaction is
    continuity, not chrome: do **not** resolve fold credentials here;
    ``run_compaction_llm`` follows the conversation's chat payer. The compaction
    branch below remains for callers that still ask this function. Chat purpose
    stays user-key-first unless the account default is an explicit platform pointer.

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

    if purpose == "title":
        if platform_catalog_visible():
            followed = await _title_config_when_catalog_open(
                session, user_id, conversation_id=conversation_id
            )
            if followed is not None:
                return followed
        return await resolve_background_user_fallback(session, user_id, purpose)

    if purpose == "compaction":
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
