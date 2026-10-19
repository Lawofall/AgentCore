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
Product identity is the catalog ``ref`` (``@platform/{id}`` / ``@byok/{provider_id}/{id}``).
Internally the row is still ``(id, origin, provider_id)`` — the SAME model id may appear
under several providers (and once more as a platform row), because「run model X on provider
A」vs「on provider B」vs「on platform free quota」are genuinely different options.
Composer pickers flatten those rows. Picker membership is origin-polarity:
platform rows default on (``hidden_refs`` opt-out); BYOK rows default off
(``enabled_refs`` opt-in). Connecting a provider does not dump its directory
into the composer. Inference ignores both sets.

* **byok** rows — per provider: vendor presets by saved ``vendor_id``
  ``∪`` proxied ``GET /models`` (cached ~10min per
  ``(provider_id, base_url)``). Custom vendor uses discovery
  only (empty catalog if the list fails). Hand-fill is always allowed on BYOK
  (empty picker body, or Settings · 模型 when the catalog has rows); typed ids
  land in ``enabled_refs`` and are synthesized here so Settings · 模型 can
  toggle them. 模型组合 still accepts a typed id.
  Discovery failure / empty still keeps the preset seed (never a 500). After
  the union, exact ids in the preset's ``hideFromPicker`` are omitted from new
  rows (retired official aliases); already-pinned profile slots stay runnable.
  OpenCode Go/Zen ``/responses`` ids (``gpt-`` / ``grok-`` / ``muse-*``) and
  ``/messages`` ids (Claude family / Union Alpha / Qwen plus-max) are listed
  and selectable — dispatch POSTs the matching protocol. Platform allowlist
  greys unpriced off-protocol ids; priced Claude / GPT stay selectable
  (OpenAI-compat Chat Completions via per-model credentials).
* **platform** rows — the operator platform model set when platform credentials exist.
  Unpriced allowlist ids that :func:`off_protocol_kind` flags stay listed (not
  silently dropped) but ``available=False`` with :class:`ModelUnavailableReason`.
  Priced Claude / GPT on the allowlist are selectable. No endpoint gate —
  see :func:`_platform_entry`.

A keyless user on a deployment with no platform subsidy gets an EMPTY catalog — the UI
shows an empty state that guides to 设置·模型配置 (no greyed-out「add a key」guide rows).

BYOK id set = (matched preset: seed ∪ discovery, minus hideFromPicker;
unmatched: discovery only) ∪ hand-filled ``enabled_refs`` that the upstream
list omitted. ``model_metadata`` only ENRICHES display fields.
Catalog ``vision`` is stamped there from
:mod:`agentcore.llm.image_accept`. Pricing reuses the community chain
(:func:`pricing_for_model`).
OpenCode ``/responses`` and ``/messages`` ids are selectable on BYOK.
Platform greys unpriced members of those families (no per-row endpoint at
catalog time); priced allowlist ids stay selectable.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
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
    hide_from_picker_ids_for_vendor,
    is_openai_chat_model_id,
    off_protocol_kind,
    preset_for_vendor,
    preset_models_for_vendor,
    seed_model_for_vendor,
)
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.model_metadata import model_metadata_for
from agentcore.llm.pricing import (
    CURRENCY_CNY,
    CredentialSource,
    decimal_wire,
    has_curated_pricing,
    pricing_for_model,
    quota_multiplier_for,
    sell_multiplier_for,
)
from agentcore.llm.profiles import PLATFORM_MODEL_FLASH
from agentcore.llm.resolve import (
    _decrypt_provider,
    list_user_providers,
    resolve_account_default_model,
)

logger = get_logger(__name__)

ModelOrigin = Literal["byok", "platform"]

# Settings channel title for origin=platform. Catalog rows no longer stamp this
# as a chip/row badge; curated SKU marks (e.g. 免费额度) still pass through.
PLATFORM_ORIGIN_BADGE = "平台"

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

    @property
    def ref(self) -> str:
        from agentcore.llm.model_ref import format_model_ref

        return format_model_ref(self.origin, self.id, self.provider_id)


@dataclass(frozen=True)
class ModelUnavailableReason:
    """Why a listed catalog row is not selectable (never a silent drop)."""

    code: Literal["upstream_protocol_unsupported"]
    required_protocol: OffProtocolKind


@dataclass(frozen=True)
class CatalogReasoningEffort:
    """Official vendor thinking-effort control tokens for one catalog row."""

    options: tuple[str, ...]
    default: str


@dataclass(frozen=True)
class ModelCatalogEntry:
    """One selectable (or grey-out) model row for the catalog UI."""

    id: str
    origin: ModelOrigin
    display_name: str
    vendor: str
    capabilities: list[str] = field(default_factory=list)
    context_length: int | None = None
    # Curated SKU badge (e.g. 免费额度). Platform origin is unmarked.
    badge: str | None = None
    price: dict[str, str] | None = None
    available: bool = True
    # BYOK rows: which 服务商 this row runs on (None for platform / guide rows).
    provider_id: str | None = None
    provider_label: str | None = None
    # Set when listed but not selectable (e.g. gateway lacks the upstream protocol).
    unavailable_reason: ModelUnavailableReason | None = None
    # Official vendor effort tokens when this id sends ``reasoning_effort``.
    reasoning_effort: CatalogReasoningEffort | None = None
    # True when this BYOK row came from enabled_refs, not seed ∪ GET /models.
    custom: bool = False

    @property
    def ref(self) -> str:
        from agentcore.llm.model_ref import format_model_ref

        return format_model_ref(self.origin, self.id, self.provider_id)


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
    multiplier = sell_multiplier_for(model_id, credential_source=credential_source)
    payload: dict[str, str] = {
        **{key: decimal_wire(value) for key, value in card.items()},
        "currency": CURRENCY_CNY,
        "multiplier": decimal_wire(multiplier),
    }
    quota = quota_multiplier_for(model_id, credential_source=credential_source)
    if quota is not None:
        payload["quota_multiplier"] = decimal_wire(quota)
    return payload


def _entry(
    model_id: str,
    *,
    origin: ModelOrigin,
    available: bool,
    credential_source: CredentialSource,
    provider_id: str | None = None,
    provider_label: str | None = None,
    unavailable_reason: ModelUnavailableReason | None = None,
    custom: bool = False,
) -> ModelCatalogEntry:
    meta = model_metadata_for(model_id)
    from agentcore.llm.provider.wire_dialect import reasoning_effort_spec

    spec = reasoning_effort_spec(model_id)
    curated = (meta.badge or "").strip() or None
    return ModelCatalogEntry(
        id=model_id,
        origin=origin,
        display_name=meta.display_name,
        vendor=meta.vendor,
        capabilities=sorted(meta.capabilities),
        context_length=meta.context_length,
        badge=curated,
        price=_price_card(model_id, credential_source=credential_source),
        available=available,
        provider_id=provider_id,
        provider_label=provider_label,
        unavailable_reason=unavailable_reason,
        reasoning_effort=(
            CatalogReasoningEffort(options=spec[0], default=spec[1]) if spec else None
        ),
        custom=custom,
    )


def _off_protocol_unavailable(model_id: str) -> ModelUnavailableReason | None:
    """Grey unpriced off-protocol allowlist ids.

    Priced ids stay selectable: the platform meter is OpenAI-compat Chat
    Completions (per-model credentials), not native ``/messages`` or
    ``/responses``.
    """
    if has_curated_pricing(model_id):
        return None
    protocol = off_protocol_kind(model_id)
    if protocol is None:
        return None
    return ModelUnavailableReason(
        code="upstream_protocol_unsupported",
        required_protocol=protocol,
    )


def _off_protocol_reason(model_id: str, base_url: str) -> ModelUnavailableReason | None:
    """BYOK catalog no longer greys protocols we speak.

    OpenCode ``/messages`` and ``/responses`` dispatch; OpenAI vendor chat
    turns use ``/responses`` regardless of ``base_url``. Signature kept so
    tests pin the no-op. Platform greying is :func:`_off_protocol_unavailable`.
    """
    del model_id, base_url
    return None


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
    """One provider's byok rows: seed ∪ discovery (discovery only if unmatched)."""
    presets = preset_models_for_vendor(creds.vendor_id)
    discovered_ids = discovered if discovered is not None else []
    preset = preset_for_vendor(creds.vendor_id)
    if preset is not None:
        ids = _dedupe([*presets, *discovered_ids])
        hidden = hide_from_picker_ids_for_vendor(creds.vendor_id)
        if hidden:
            ids = [mid for mid in ids if mid not in hidden]
        if preset.id == "openai":
            ids = [mid for mid in ids if is_openai_chat_model_id(mid)]
    else:
        ids = _dedupe(list(discovered_ids))
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
    """Brand display name for a platform catalog row. Badge is a separate field."""
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

    Unpriced off-protocol ids use :func:`off_protocol_kind` (family + exact
    overrides) and are listed but not selectable. Priced Claude / GPT on the
    allowlist stay selectable (OpenAI-compat Chat Completions).
    **No base_url gate** on this path: platform rows have no per-row endpoint
    at catalog time (credentials resolve later via the operator pool /
    per-model override / ``PLATFORM_BASE_URL``), and the default URL is
    DeepSeek — gating on that URL would miss the allowlist misconfig this
    exists to catch. BYOK OpenCode lists both protocols as selectable.
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

    Selectable 上架 set (priced allowlist / fallback) plus off-protocol
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


def _add_enabled_custom_models(
    *,
    providers,
    enabled_refs: Sequence[str],
    add,
) -> None:
    """Synthesize BYOK rows for picker allowlist ids the upstream list omitted."""
    from agentcore.llm.model_ref import parse_model_input

    known = {row.id: row for row in providers}
    for ref in enabled_refs:
        parsed = parse_model_input(ref)
        if parsed.kind != "ref" or parsed.origin != "byok":
            continue
        row = known.get(parsed.provider_id)
        if row is None:
            continue
        label = (row.label or "").strip() or None
        add(
            _entry(
                parsed.model,
                origin="byok",
                available=True,
                credential_source="user",
                provider_id=row.id,
                provider_label=label,
                custom=True,
            )
        )


async def resolve_model_catalog(
    session: AsyncSession,
    user_id: str,
    *,
    enabled_refs: Sequence[str] | None = None,
) -> ModelCatalog:
    """The user's unified model catalog: current default, BYOK flag, and all rows.

    Every active provider is discovered independently and its models mixed in, tagged
    with that provider's id/label. Platform rows appear only when
    :func:`platform_catalog_visible` (billing selectable ∧ credentials configured).

    Custom BYOK picker rows come from ``enabled_refs`` (caller-loaded). This
    gateway does not open a session to read them; omit the argument for none.
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
            # Undecryptable provider (rotated master key / corrupt cipher): still
            # surface a preset seed row so settings shows the 服务商, greyed out.
            seed = seed_model_for_vendor(row.vendor_id)
            if not seed:
                continue
            label = (row.label or "").strip() or None
            _add(
                _entry(
                    seed,
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

    _add_enabled_custom_models(
        providers=providers, enabled_refs=enabled_refs or (), add=_add
    )

    for entry in _platform_entries(require_visible=True):
        _add(entry)

    return ModelCatalog(current=current, byok_configured=True, models=models)


async def validate_model_choice(
    session: AsyncSession,
    user_id: str,
    model: str,
    origin: ModelOrigin,
    provider_id: str | None = None,
    *,
    enabled_refs: Sequence[str] | None = None,
) -> bool:
    """Whether ``(model, origin, provider_id)`` is valid + available in the catalog.

    For byok rows the provider must match too (the same id under a different provider
    is a different option). ``provider_id`` is ignored for platform rows (always None).
    Pass ``enabled_refs`` so hand-filled BYOK ids participate; the gateway
    does not load them.
    """
    target = (model or "").strip()
    if not target or origin not in ("byok", "platform"):
        return False
    want_provider = provider_id if origin == "byok" else None
    catalog = await resolve_model_catalog(
        session, user_id, enabled_refs=enabled_refs
    )
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
