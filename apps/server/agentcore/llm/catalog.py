"""User-facing model catalog (模型目录 · 统一混排 多 BYOK 服务商 + 平台).

**Sole fact source** for「which models exist / are listable / how they display in
pickers」. Display enrichment lives in :mod:`agentcore.llm.model_metadata` (never an
allow-list); this module owns discovery + platform 上架 + row assembly.
:mod:`agentcore.llm.model_profiles` **derives** system presets / combo views from
:func:`platform_listable_model_ids` / :func:`visible_platform_listable_model_ids` —
it must not maintain a parallel model set.

Resolves the set of models a user may pick, plus the account's currently-resolved
model, for ``GET /v1/users/me/models`` and the conversation-model PATCH validation.

Each catalog row carries ``origin`` (``byok`` | ``platform``) and, for byok rows, the
``provider_id`` + ``provider_label`` of the exact 服务商 it lives under.
``(id, origin, provider_id)`` is the unique key — the SAME model id may appear under
several providers (and once more as a platform row), because「run model X on provider
A」vs「on provider B」vs「on platform free quota」are genuinely different options.

* **byok** rows — per provider: ``default_model ∪`` vendor presets matched by
  normalized ``base_url`` ``∪`` proxied ``GET /models`` (cached ~10min per
  ``(provider_id, base_url)``). Discovery failure / empty still keeps preset + default
  (never a 500); unknown/custom ``base_url`` has no preset (default + discovery only).
  OpenCode Go/Zen ids in the shared off-protocol map
  (:data:`agentcore.llm.byok_provider_presets.BYOK_OFF_PROTOCOL_MODELS`) stay
  listed (not silently dropped) but ``available=False`` with
  :class:`ModelUnavailableReason`.
* **platform** rows — the operator platform model set when platform credentials exist.
  Allowlist ids in the same off-protocol map stay listed (not silently dropped)
  but ``available=False`` with :class:`ModelUnavailableReason`. No endpoint
  gate — see :func:`_platform_entry`.

A keyless user on a deployment with no platform subsidy gets an EMPTY catalog — the UI
shows an empty state that guides to 设置·模型配置 (no greyed-out「add a key」guide rows).

BYOK id set = default ∪ base_url presets ∪ discovery; ``model_metadata`` only
ENRICHES display fields. Pricing reuses the community chain (:func:`pricing_for_model`).
Off-protocol OpenCode ids are kept in that set (visible, not selectable).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.billing.preference import (
    platform_catalog_visible,
    platform_model_allowlist,
)
from agentcore.config import settings
from agentcore.core.logging import get_logger
from agentcore.llm.byok_provider_presets import (
    OffProtocolKind,
    is_opencode_byok_endpoint,
    off_protocol_kind,
    preset_models_for_base_url,
)
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.model_metadata import model_metadata_for
from agentcore.llm.pricing import (
    CredentialSource,
    has_curated_pricing,
    pricing_for_model,
)
from agentcore.llm.profiles import PLATFORM_MODEL_FLASH
from agentcore.llm.resolve import (
    _decrypt_provider,
    list_user_providers,
    resolve_account_default_model,
)

logger = get_logger(__name__)

ModelOrigin = Literal["byok", "platform"]

# BYOK discovery cache: (provider_id, base_url) -> (monotonic_expiry, model_ids).
# Keyed by provider so each 服务商 discovers independently; base_url in the key busts
# the cache when an endpoint is re-pointed.
_DISCOVERY_TTL_SEC = 600.0
_discovery_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}


@dataclass(frozen=True)
class ModelCatalogCurrent:
    id: str
    origin: ModelOrigin
    provider_id: str | None = None


@dataclass(frozen=True)
class ModelUnavailableReason:
    """Why a listed catalog row is not selectable (never a silent drop)."""

    code: Literal["upstream_protocol_unsupported"]
    required_protocol: OffProtocolKind


@dataclass(frozen=True)
class ModelCatalogEntry:
    """One selectable (or grey-out) model row for the catalog UI."""

    id: str
    origin: ModelOrigin
    display_name: str
    vendor: str
    capabilities: list[str] = field(default_factory=list)
    context_length: int | None = None
    # Curated display badge (passthrough from model_metadata); None when unset.
    badge: str | None = None
    price: dict[str, str] | None = None
    available: bool = True
    # BYOK rows: which 服务商 this row runs on (None for platform / guide rows).
    provider_id: str | None = None
    provider_label: str | None = None
    # Set when listed but not selectable (e.g. gateway lacks the upstream protocol).
    unavailable_reason: ModelUnavailableReason | None = None


@dataclass(frozen=True)
class ModelCatalog:
    current: ModelCatalogCurrent
    byok_configured: bool
    models: list[ModelCatalogEntry]


def _price_card(
    model_id: str,
    *,
    credential_source: CredentialSource,
) -> dict[str, str] | None:
    card = pricing_for_model(model_id, credential_source=credential_source)
    if card is None:
        return None
    return {key: str(value) for key, value in card.items()}


def _entry(
    model_id: str,
    *,
    origin: ModelOrigin,
    available: bool,
    credential_source: CredentialSource,
    provider_id: str | None = None,
    provider_label: str | None = None,
    unavailable_reason: ModelUnavailableReason | None = None,
) -> ModelCatalogEntry:
    meta = model_metadata_for(model_id)
    return ModelCatalogEntry(
        id=model_id,
        origin=origin,
        display_name=meta.display_name,
        vendor=meta.vendor,
        capabilities=sorted(meta.capabilities),
        context_length=meta.context_length,
        badge=meta.badge,
        price=_price_card(model_id, credential_source=credential_source),
        available=available,
        provider_id=provider_id,
        provider_label=provider_label,
        unavailable_reason=unavailable_reason,
    )


def _off_protocol_unavailable(model_id: str) -> ModelUnavailableReason | None:
    """Structured reason when ``model_id`` is in the shared exact-id off-protocol map."""
    protocol = off_protocol_kind(model_id)
    if protocol is None:
        return None
    return ModelUnavailableReason(
        code="upstream_protocol_unsupported",
        required_protocol=protocol,
    )


def _off_protocol_reason(model_id: str, base_url: str) -> ModelUnavailableReason | None:
    """OpenCode Go/Zen only: known off-protocol ids are listed but not selectable."""
    if not is_opencode_byok_endpoint(base_url):
        return None
    return _off_protocol_unavailable(model_id)


def _dedupe(ids: list[str]) -> list[str]:
    """Order-preserving de-dupe (dict.fromkeys), dropping blanks."""
    return list(dict.fromkeys(mid for mid in ids if mid))


async def _discover_provider_models(row, creds: LLMCredentials) -> list[str] | None:
    """Proxy one provider's ``GET /models``; cached ~10min. ``None`` on any failure."""
    cache_key = (row.id, creds.base_url)
    now = time.monotonic()
    cached = _discovery_cache.get(cache_key)
    if cached is not None and cached[0] > now:
        return list(cached[1])

    from agentcore.llm.factory import build_provider

    provider = build_provider(creds)
    try:
        ids = await provider.list_models()
    except Exception as e:  # noqa: BLE001 — discovery is best-effort; degrade on any error
        logger.info(
            "model_catalog.discovery_failed",
            provider_id=row.id,
            base_url=creds.base_url,
            error=str(e),
        )
        return None
    finally:
        await provider.close()

    _discovery_cache[cache_key] = (now + _DISCOVERY_TTL_SEC, list(ids))
    return ids


def _provider_entries(
    row, creds: LLMCredentials, discovered: list[str] | None
) -> list[ModelCatalogEntry]:
    """One provider's byok rows: default ∪ base_url preset ∪ discovered, tagged with provider."""
    current = (creds.default_model or "").strip() or PLATFORM_MODEL_FLASH
    presets = preset_models_for_base_url(creds.base_url)
    discovered_ids = discovered if discovered is not None else []
    ids = _dedupe([current, *presets, *discovered_ids])
    label = (row.label or "").strip() or None
    entries: list[ModelCatalogEntry] = []
    for mid in ids:
        reason = _off_protocol_reason(mid, creds.base_url)
        entries.append(
            _entry(
                mid,
                origin="byok",
                available=reason is None,
                credential_source="user",
                provider_id=row.id,
                provider_label=label,
                unavailable_reason=reason,
            )
        )
    return entries


def _platform_model_ids() -> list[str]:
    """The platform catalog's configured model ids (成本配额与计费 §〇·六 F3).

    Explicit ``PLATFORM_MODELS`` allowlist when set (the flip's curated set); else the
    single ``platform_model`` (+ background) fallback — byok / free-tier deployments
    keep their one dormant platform row unchanged. Does **not** filter by curated
    pricing — see :func:`platform_listable_model_ids` for the 上架 set.
    """
    allowlist = platform_model_allowlist()
    if allowlist:
        return _dedupe(allowlist)
    platform_model = (settings.platform_model or "").strip() or PLATFORM_MODEL_FLASH
    background = (settings.platform_background_model or "").strip()
    return _dedupe([platform_model, background])


def platform_listable_model_ids() -> list[str]:
    """Allowlist / fallback ids that may appear in catalog and system presets (F4).

    **Public fact source** for the platform 上架 set (selectable catalog + presets).
    Missing curated price card → hard-exclude (不上架); log once per id for ops.
    Off-protocol allowlist ids skip that warning — they list as unavailable via
    :func:`_platform_catalog_ids`. Does **not** apply the billing visibility gate
    — see :func:`visible_platform_listable_model_ids`.
    """
    listable: list[str] = []
    for mid in _platform_model_ids():
        if has_curated_pricing(mid):
            listable.append(mid)
        elif off_protocol_kind(mid) is None:
            logger.warning("platform_catalog.pricing_missing", model=mid)
    return listable


def is_platform_listable(model_id: str) -> bool:
    """Whether ``model_id`` is in the platform 上架 set (ignores billing visibility)."""
    target = (model_id or "").strip()
    if not target:
        return False
    return target in platform_listable_model_ids()


def visible_platform_listable_model_ids() -> list[str]:
    """上架 set when the platform catalog gate is open; else ``[]``.

    Single conjunction for **selectable** platform 上架 / system-preset listing —
    callers must not re-check :func:`platform_catalog_visible` separately.
    Catalog assembly may additionally list off-protocol allowlist ids as
    unavailable (see :func:`_platform_catalog_ids`).
    """
    if not platform_catalog_visible():
        return []
    return platform_listable_model_ids()


def platform_model_display_name(model_id: str) -> str:
    """Display name via the same enrichment path catalog rows use (``model_metadata``)."""
    return model_metadata_for(model_id).display_name


def platform_model_label(model_id: str) -> str:
    """Display name with the curated badge folded in, for one-string surfaces.

    Curated uniqueness is ``(display_name, badge)``, so the bare display name repeats
    across SKUs of one model (free vs priced Flash). Surfaces that render a lone label
    with no badge slot — e.g. 模型组合名 — must use this to stay distinguishable;
    catalog rows carry ``badge`` as its own field and keep using
    :func:`platform_model_display_name`.
    """
    meta = model_metadata_for(model_id)
    badge = (meta.badge or "").strip()
    if not badge:
        return meta.display_name
    return f"{meta.display_name} · {badge}"


def _platform_entry(model_id: str) -> ModelCatalogEntry:
    """One platform-billed catalog row (nominal-price ledger, F4).

    Off-protocol ids use the shared exact map (:func:`off_protocol_kind`) and are
    listed but not selectable. **No base_url gate** on this path: platform rows
    have no per-row endpoint at catalog time (credentials resolve later via the
    operator pool / per-model override / ``PLATFORM_BASE_URL``), and the default
    URL is DeepSeek — gating on that URL would miss the allowlist misconfig this
    exists to catch. BYOK still gates on OpenCode endpoints because the same id
    can be chat/completions on another user-configured relay.
    """
    reason = _off_protocol_unavailable(model_id)
    return _entry(
        model_id,
        origin="platform",
        available=reason is None,
        credential_source="platform",
        unavailable_reason=reason,
    )


def _platform_catalog_ids(*, require_visible: bool = False) -> list[str]:
    """Ids that become platform catalog rows.

    Selectable 上架 set (priced allowlist / fallback) plus exact off-protocol
    allowlist ids that pricing excluded — those list as unavailable rather than
    disappearing. System presets still read :func:`platform_listable_model_ids`
    only, so an unselectable id does not become a combo.
    """
    if require_visible and not platform_catalog_visible():
        return []
    priced = platform_listable_model_ids()
    extras = [
        mid
        for mid in _platform_model_ids()
        if off_protocol_kind(mid) is not None and mid not in priced
    ]
    return _dedupe([*priced, *extras])


def _platform_entries(*, require_visible: bool = False) -> list[ModelCatalogEntry]:
    """Platform catalog rows. ``require_visible=True`` applies the billing gate."""
    return [
        _platform_entry(mid) for mid in _platform_catalog_ids(require_visible=require_visible)
    ]


# Back-compat alias (tests / older call sites may still patch the private name).
_platform_listable_model_ids = platform_listable_model_ids


async def resolve_model_catalog(session: AsyncSession, user_id: str) -> ModelCatalog:
    """The user's unified model catalog: current default, BYOK flag, and all rows.

    Every active provider is discovered independently and its models mixed in, tagged
    with that provider's id/label. Platform rows appear only when
    :func:`platform_catalog_visible` (billing selectable ∧ credentials configured).
    """
    providers = await list_user_providers(session, user_id)
    byok_configured = len(providers) > 0

    selection = await resolve_account_default_model(session, user_id)
    current = ModelCatalogCurrent(
        id=selection.model, origin=selection.origin, provider_id=selection.provider_id
    )

    if not byok_configured:
        # Keyless: platform catalog when subsidized; otherwise an EMPTY catalog (byok
        # deployment with no platform subsidy) — the UI shows an empty state that guides
        # to 设置·模型配置 rather than greyed-out「add a key to unlock」guide rows.
        # Visibility gate is inside ``_platform_entries(require_visible=True)``.
        return ModelCatalog(
            current=current,
            byok_configured=False,
            models=_platform_entries(require_visible=True),
        )

    models: list[ModelCatalogEntry] = []
    seen: set[tuple[str, str, str | None]] = set()

    def _add(entry: ModelCatalogEntry) -> None:
        key = (entry.id, entry.origin, entry.provider_id)
        if key not in seen:
            models.append(entry)
            seen.add(key)

    for row in providers:
        creds = _decrypt_provider(row, user_id)
        if creds is None:
            # Undecryptable provider (rotated master key / corrupt cipher): still surface
            # its default model row so the settings UI shows the provider, greyed out.
            label = (row.label or "").strip() or None
            _add(
                _entry(
                    (row.default_model or "").strip() or PLATFORM_MODEL_FLASH,
                    origin="byok",
                    available=False,
                    credential_source="user",
                    provider_id=row.id,
                    provider_label=label,
                )
            )
            continue
        discovered = await _discover_provider_models(row, creds)
        for entry in _provider_entries(row, creds, discovered):
            _add(entry)

    for entry in _platform_entries(require_visible=True):
        _add(entry)

    return ModelCatalog(current=current, byok_configured=True, models=models)


async def validate_model_choice(
    session: AsyncSession,
    user_id: str,
    model: str,
    origin: ModelOrigin,
    provider_id: str | None = None,
) -> bool:
    """Whether ``(model, origin, provider_id)`` is valid + available in the catalog.

    For byok rows the provider must match too (the same id under a different provider
    is a different option). ``provider_id`` is ignored for platform rows (always None).
    """
    target = (model or "").strip()
    if not target or origin not in ("byok", "platform"):
        return False
    want_provider = provider_id if origin == "byok" else None
    catalog = await resolve_model_catalog(session, user_id)
    return any(
        entry.id == target
        and entry.origin == origin
        and entry.provider_id == want_provider
        and entry.available
        for entry in catalog.models
    )


def reset_discovery_cache_for_tests() -> None:
    """Clear the BYOK discovery cache (test isolation)."""
    _discovery_cache.clear()
