"""Credential resolution + profile-expand consumption for every LLM call site.

BYOK is a **list of providers** (``user_llm_providers``). Account / conversation
select a **model combination profile**; this module expands via
:class:`~agentcore.llm.model_profiles.LlmModelProfileService` (derived query layer)
then resolves upstream credentials. Model **metadata / 上架** live in
:mod:`agentcore.llm.catalog` (+ :mod:`agentcore.llm.model_metadata`) — not here.

Scope: credentials, decrypt, platform key wiring, and expand→creds helpers.
Strategy (purpose→model priority, turn profiles, model×params pairing) lives in
:mod:`agentcore.llm.model_selection` — this module keeps thin re-exports for
call-site / test compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from agentcore.db.models import UserLlmProvider

from agentcore.config import settings
from agentcore.config.platform import parse_platform_model_credentials
from agentcore.core.logging import get_logger
from agentcore.llm.byok_provider_presets import seed_model_for_base_url
from agentcore.llm.credentials import LLMCredentials, derive_platform_credential_id

logger = get_logger(__name__)

ProviderPurpose = Literal["user_facing", "platform_internal"]
ModelPurpose = str  # chat | title | compaction | file.rewrite | ...
ModelOrigin = Literal["byok", "platform"]

__all__ = [
    "ModelConfig",
    "ModelOrigin",
    "ModelSelection",
    "ProviderPurpose",
    "platform_llm_credentials",
    "platform_wire_model",
    "resolve_account_default_model",
    "resolve_account_worker_selection",
    "resolve_background_user_fallback",
    "resolve_conversation_model_selection",
    "resolve_credentials",
    "resolve_explicit_background_byok",
    "resolve_model_config",
    "resolve_provider_credentials",
    "resolve_turn_model",
    "resolve_user_chat_model",
    "resolve_user_llm_credentials",
    "list_user_providers",
    "user_has_provider",
]


@dataclass(frozen=True)
class ModelSelection:
    model: str
    origin: ModelOrigin
    # The BYOK provider this selection runs on (None for platform / keyless).
    provider_id: str | None = None


@dataclass(frozen=True)
class ModelConfig:
    model: str
    base_url: str
    api_key: str
    source: Literal["platform", "byok"]
    purpose: str
    background_model: str | None = None
    provider_id: str | None = None
    # BYOK ``user_llm_providers.label`` — preserved across ModelConfig ↔ credentials hops.
    label: str | None = None


def _encryptor():
    from agentcore.security.keys import KeyEncryptor

    if not settings.encryption_key:
        return None
    try:
        return KeyEncryptor(settings.encryption_key)
    except ValueError:
        # Malformed master key must not 500 chat / catalog paths — degrade like
        # a missing key (provider_service raises KeyStorageUnavailableError on write).
        logger.error("byok.key_malformed")
        return None


def platform_llm_credentials(model: str | None = None) -> LLMCredentials | None:
    """Platform upstream credentials — the single point of per-model resolution.

    ``model`` selects a per-model override (运营中转「一 key 一模型」, 成本配额与计费
    §〇·六 F3): when the id has an entry in ``platform_model_credentials`` **with its
    own api_key**, that pair wins (missing ``base_url`` falls back to the shared
    default). Otherwise the operator pool is fill-first among schedulable members
    (sticky on ``conversation_id``; cooling / exhausted / blocked skipped). Key
    and ``base_url`` stay bound; the global ``PLATFORM_BASE_URL`` is not mixed in. An
    empty / all-disabled pool falls back to ``platform_api_key`` / ``platform_base_url``
    (and a base_url-only override still applies on that env path). A non-empty pool
    that is entirely unschedulable does **not** fall back to env. A no-arg call
    uses ``default_model=platform_model``. Returns ``None`` when no usable key
    resolves for this model.
    """
    entry: dict[str, str] = {}
    if model:
        entry = parse_platform_model_credentials(settings.platform_model_credentials).get(
            model, {}
        )
    override_key = (entry.get("api_key") or "").strip()
    if override_key:
        base_url = (entry.get("base_url") or "").strip() or settings.platform_base_url
        return LLMCredentials(
            api_key=override_key,
            base_url=base_url,
            default_model=model or settings.platform_model,
            source="platform",
            platform_credential_id=derive_platform_credential_id(override_key, base_url) or None,
        )

    from agentcore.llm.platform_pool_scheduler import (
        pick_last_resort_platform_pool_member,
        pick_schedulable_platform_pool_member,
        pool_has_enabled_members,
    )

    member = pick_schedulable_platform_pool_member()
    if member is None and pool_has_enabled_members():
        # Pool is in use: never fall through to PLATFORM_API_KEY. A cooling
        # member lets the leaf raise the existing 429 CTA; all-blocked → None.
        member = pick_last_resort_platform_pool_member()
        if member is None:
            return None
    if member is not None:
        return LLMCredentials(
            api_key=member.api_key,
            base_url=member.base_url,
            default_model=model or settings.platform_model,
            source="platform",
            platform_credential_id=member.id,
        )

    api_key = settings.platform_api_key.strip()
    if not api_key:
        return None
    base_url = (entry.get("base_url") or "").strip() or settings.platform_base_url
    return LLMCredentials(
        api_key=api_key,
        base_url=base_url,
        default_model=model or settings.platform_model,
        source="platform",
        platform_credential_id=derive_platform_credential_id(api_key, base_url) or None,
    )


def platform_wire_model(model: str) -> str:
    """Catalog id → id sent to the platform upstream (optional ``upstream_model`` override).

    Lookup uses the catalog id; billing / profiles keep the catalog id. Only the HTTP
    ``model`` field is remapped (see ``PlatformProvider``).
    """
    mid = (model or "").strip()
    if not mid:
        return mid
    entry = parse_platform_model_credentials(settings.platform_model_credentials).get(mid, {})
    upstream = (entry.get("upstream_model") or "").strip()
    return upstream or mid


# --- provider row helpers ----------------------------------------------------


def _credentials_from_provider(row: UserLlmProvider, api_key: str) -> LLMCredentials:
    label = (row.label or "").strip() or None
    return LLMCredentials(
        api_key=api_key,
        base_url=row.base_url or settings.platform_base_url,
        default_model=seed_model_for_base_url(row.base_url or ""),
        source="user",
        provider_id=row.id,
        label=label,
    )


def _decrypt_provider(row: UserLlmProvider, user_id: str) -> LLMCredentials | None:
    """Decrypt a provider row's key into ``LLMCredentials`` (None on any failure)."""
    if not row.api_key_enc:
        return None
    enc = _encryptor()
    if enc is None:
        return None
    try:
        api_key = enc.decrypt(row.api_key_enc).decode()
    except Exception as e:  # noqa: BLE001 — corrupt cipher / rotated master key degrades to None
        logger.warning("byok.decrypt_failed", user_id=user_id, provider_id=row.id, error=str(e))
        return None
    return _credentials_from_provider(row, api_key)


async def _load_provider(
    session: AsyncSession, user_id: str, provider_id: str | None
) -> UserLlmProvider | None:
    """Owner-scoped provider fetch by id (None for a missing / non-owned / dangling id)."""
    if not provider_id:
        return None
    from agentcore.db.repositories import UserLlmProviderRepository

    return await UserLlmProviderRepository(session).get(provider_id, user_id=user_id)


async def _default_chat_provider_row(
    session: AsyncSession, user_id: str, *, user=None
) -> UserLlmProvider | None:
    """The account's default BYOK provider row for chat (profile main → first provider).

    Used as a low-level fallback when expanding slots / decrypting without a full
    profile walk. Prefer ``resolve_account_default_model`` for turn selection.
    """
    from agentcore.db.repositories import (
        LlmModelProfileRepository,
        UserLlmProviderRepository,
        UserRepository,
    )

    repo = UserLlmProviderRepository(session)
    if user is None:
        user = await UserRepository(session).get_by_id(user_id)
    assembly_id = getattr(user, "default_assembly_id", None) if user is not None else None
    if assembly_id:
        row_assembly = await LlmModelProfileRepository(session).get(
            assembly_id, user_id=user_id
        )
        if (
            row_assembly is not None
            and row_assembly.main_origin == "byok"
            and row_assembly.main_provider_id
        ):
            row = await repo.get(row_assembly.main_provider_id, user_id=user_id)
            if row is not None:
                return row
    return await repo.first_for_user(user_id)


async def _account_default(
    session: AsyncSession, user_id: str
) -> tuple[UserLlmProvider | None, str, ModelOrigin]:
    """(provider_row, model, origin) for the account default chat selection via profile."""
    selection = await resolve_account_default_model(session, user_id)
    row = None
    if selection.origin == "byok" and selection.provider_id:
        row = await _load_provider(session, user_id, selection.provider_id)
    return row, selection.model, selection.origin


async def user_has_provider(session: AsyncSession, user_id: str) -> bool:
    """Whether the user has at least one configured BYOK provider."""
    from agentcore.db.repositories import UserLlmProviderRepository

    return await UserLlmProviderRepository(session).count_for_user(user_id) > 0


async def list_user_providers(session: AsyncSession, user_id: str) -> list[UserLlmProvider]:
    """All of a user's BYOK provider rows (the catalog reads these to discover models).

    Lives on the resolve bridge (the single llm↔db seam) so the catalog stays a pure
    llm-layer module.
    """
    from agentcore.db.repositories import UserLlmProviderRepository

    return list(await UserLlmProviderRepository(session).list_for_user(user_id))


async def resolve_provider_credentials(
    session: AsyncSession, user_id: str, provider_id: str
) -> LLMCredentials | None:
    """Decrypt a specific provider's credentials (owner-scoped). None if missing/undecryptable."""
    row = await _load_provider(session, user_id, provider_id)
    if row is None:
        return None
    return _decrypt_provider(row, user_id)


async def resolve_user_llm_credentials(
    session: AsyncSession, user_id: str, *, provider_id: str | None = None
) -> LLMCredentials | None:
    """BYOK credentials for a ``provider_id``, or the account's default chat provider.

    Kept as the general「the user's BYOK credentials」entry point: callers that pin a
    provider pass ``provider_id``; callers that just want the account default omit it.
    """
    if provider_id:
        return await resolve_provider_credentials(session, user_id, provider_id)
    row = await _default_chat_provider_row(session, user_id)
    if row is None:
        return None
    return _decrypt_provider(row, user_id)


async def resolve_account_default_model(session: AsyncSession, user_id: str) -> ModelSelection:
    """Account default main slot (default profile / system glm-5.2 preset)."""
    from agentcore.llm.model_profiles import LlmModelProfileService

    expanded = await LlmModelProfileService(session).expand(user_id, None)
    return expanded.main


async def resolve_conversation_model_selection(
    session: AsyncSession,
    conv,
    user_id: str,
) -> ModelSelection:
    """Resolve main model + origin + provider for a user-facing turn (profile expand).

    ``conversations.assembly_id`` when set; else account ``default_assembly_id``;
    else system glm-5.2 preset. Live expand — dangling provider pins fall back silently.
    """
    from agentcore.llm.model_profiles import LlmModelProfileService

    expanded = await LlmModelProfileService(session).expand_for_conversation(user_id, conv)
    return expanded.main


async def resolve_account_worker_selection(
    session: AsyncSession,
    user_id: str,
    *,
    conv=None,
) -> ModelSelection | None:
    """Worker slot from the effective profile, or None when follow_main.

    When ``conv`` is given, uses that conversation's profile pin (else account default).
    """
    from agentcore.llm.model_profiles import LlmModelProfileService

    svc = LlmModelProfileService(session)
    if conv is not None:
        expanded = await svc.expand_for_conversation(user_id, conv)
    else:
        expanded = await svc.expand(user_id, None)
    return expanded.worker


async def _resolve_background(
    session: AsyncSession, user_id: str, *, allow_platform_origin: bool = True
) -> tuple[LLMCredentials, str] | None:
    """Account default profile's background slot → ``(creds, model)``, or None (follow).

    When ``allow_platform_origin`` is False, a combo background slot that points at
    platform is treated as absent (user-BYOK auth-fallback path).
    """
    from agentcore.llm.model_profiles import LlmModelProfileService

    expanded = await LlmModelProfileService(session).expand(user_id, None)
    bg = expanded.background
    if bg is None:
        return None
    if bg.origin == "platform":
        if not allow_platform_origin:
            return None
        creds = platform_llm_credentials(model=bg.model)
        if creds is None:
            return None
        return creds, bg.model
    if not bg.provider_id:
        return None
    row = await _load_provider(session, user_id, bg.provider_id)
    if row is None:
        return None
    creds = _decrypt_provider(row, user_id)
    if creds is None:
        return None
    return creds, bg.model


def _model_config_from_creds(creds: LLMCredentials, model: str, purpose: str) -> ModelConfig:
    return ModelConfig(
        model=model,
        base_url=creds.base_url,
        api_key=creds.api_key,
        source="byok" if creds.source != "platform" else "platform",
        purpose=purpose,
        provider_id=creds.provider_id,
        label=creds.label,
    )


async def resolve_explicit_background_byok(
    session: AsyncSession,
    user_id: str,
    purpose: ModelPurpose = "title",
) -> ModelConfig | None:
    """The account combo's background slot when the user pointed it at their own key.

    ``None`` means there is no such slot to honour: unset (follow_main), pointing at
    platform, or its 服务商 has since been deleted / no longer decrypts. Callers keep
    their own default path in that case. Model id is passed through as configured.
    """
    bg = await _resolve_background(session, user_id, allow_platform_origin=False)
    if bg is None:
        return None
    creds, model = bg
    return _model_config_from_creds(creds, model, purpose)


async def resolve_background_user_fallback(
    session: AsyncSession,
    user_id: str,
    purpose: ModelPurpose = "title",
) -> ModelConfig | None:
    """BYOK-only background resolve after platform is unavailable or auth-rejected.

    Skips the platform key and any combination-profile background slot with
    ``origin=platform``. Never an authorization path — pair with
    ``resolve_and_gate_background_user_fallback`` (source=user, no platform quota).
    """
    from agentcore.llm.model_selection import _model_for_purpose

    explicit = await resolve_explicit_background_byok(session, user_id, purpose)
    if explicit is not None:
        return explicit
    row, chat_model, _origin = await _account_default(session, user_id)
    if row is not None:
        creds = _decrypt_provider(row, user_id)
        if creds is not None:
            model = _model_for_purpose(purpose, chat_model=chat_model, user_background_model=None)
            return _model_config_from_creds(creds, model, purpose)
    return None


async def resolve_model_config(
    session: AsyncSession,
    user_id: str,
    purpose: ModelPurpose = "chat",
) -> ModelConfig | None:
    """Thin re-export — strategy lives in :func:`model_selection.select_model_config`."""
    from agentcore.llm.model_selection import select_model_config

    return await select_model_config(session, user_id, purpose)


async def resolve_credentials(
    session: AsyncSession,
    user_id: str,
    purpose: ProviderPurpose = "user_facing",
) -> LLMCredentials | None:
    """Legacy credential carrier for factory / route preflight."""
    scenario = "chat" if purpose == "user_facing" else "title"
    cfg = await resolve_model_config(session, user_id, scenario)
    if cfg is None:
        return None
    return LLMCredentials(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        default_model=cfg.model,
        source="platform" if cfg.source == "platform" else "user",
        provider_id=cfg.provider_id,
        label=cfg.label,
    )


def resolve_turn_model(
    credentials: LLMCredentials | None,
    *,
    conversation_model: str | None = None,
) -> str:
    """Thin re-export — strategy lives in :func:`model_selection.select_turn_model`."""
    from agentcore.llm.model_selection import select_turn_model

    return select_turn_model(credentials, conversation_model=conversation_model)


async def resolve_user_chat_model(session: AsyncSession, user_id: str) -> str:
    """Thin re-export — strategy lives in :func:`model_selection.select_user_chat_model`."""
    from agentcore.llm.model_selection import select_user_chat_model

    return await select_user_chat_model(session, user_id)
