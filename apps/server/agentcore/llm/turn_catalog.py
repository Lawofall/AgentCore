"""Model catalog for one turn: cloud session reads Postgres; ticketed sidecar asks the cloud.

Sidecar turns must not open local Postgres. When a narrow ticket is bound, the
catalog comes from ``GET /v1/account/models`` (same resolver as
``GET /v1/users/me/models``). Same-model plans do not call this module.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.llm.catalog import (
    CatalogReasoningEffort,
    ModelCatalog,
    ModelCatalogCurrent,
    ModelCatalogEntry,
    ModelUnavailableReason,
)

logger = get_logger(__name__)

DEBATE_CATALOG_UNAVAILABLE = (
    "这一回合读不到模型目录，跨模型对阵和点名都做不了；"
    "各方 `model` 留空、不要 `cross_model`，即跟当前主模型。"
)

DELEGATE_CATALOG_UNAVAILABLE = (
    "这一回合读不到模型目录，点名模型做不了；`model` 留空即跟组合 Worker 槽。禁止 silent 回退。"
)

_ORIGINS = frozenset({"platform", "byok"})
_UNAVAILABLE_CODES = frozenset({"upstream_protocol_unsupported"})
_PROTOCOLS = frozenset({"openai_responses", "anthropic_messages"})


class TurnCatalogUnavailableError(Exception):
    """This turn asked for a catalog and could not read one."""


def catalog_from_wire(payload: dict[str, Any]) -> ModelCatalog:
    """Rebuild a :class:`ModelCatalog` from ``ModelCatalogResponse`` JSON."""
    current = payload.get("current")
    if not isinstance(current, dict):
        raise ValueError("current")
    origin = current.get("origin")
    if origin not in _ORIGINS:
        raise ValueError("current.origin")
    model_id = str(current.get("id") or "").strip()
    if not model_id:
        raise ValueError("current.id")
    provider_id = current.get("provider_id")
    if provider_id is not None:
        provider_id = str(provider_id) or None

    models: list[ModelCatalogEntry] = []
    rows = payload.get("models")
    if not isinstance(rows, list):
        raise ValueError("models")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("model row")
        models.append(_entry_from_wire(row))

    return ModelCatalog(
        current=ModelCatalogCurrent(
            id=model_id,
            origin=origin,  # type: ignore[arg-type]
            provider_id=provider_id,
        ),
        byok_configured=bool(payload.get("byok_configured")),
        models=models,
    )


def _entry_from_wire(row: dict[str, Any]) -> ModelCatalogEntry:
    origin = row.get("origin")
    if origin not in _ORIGINS:
        raise ValueError("origin")
    model_id = str(row.get("id") or "").strip()
    if not model_id:
        raise ValueError("id")
    provider_id = row.get("provider_id")
    if provider_id is not None:
        provider_id = str(provider_id) or None
    price = _price_from_wire(row.get("price"))
    return ModelCatalogEntry(
        id=model_id,
        origin=origin,  # type: ignore[arg-type]
        display_name=str(row.get("display_name") or model_id),
        vendor=str(row.get("vendor") or ""),
        capabilities=[str(c) for c in row.get("capabilities") or []],
        context_length=_optional_int(row.get("context_length")),
        badge=_optional_str(row.get("badge")),
        price=price,
        available=bool(row.get("available", True)),
        provider_id=provider_id,
        provider_label=_optional_str(row.get("provider_label")),
        unavailable_reason=_reason_from_wire(row.get("unavailable_reason")),
        reasoning_effort=_effort_from_wire(row.get("reasoning_effort")),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("context_length")
    return value


def _price_from_wire(raw: Any) -> dict[str, str] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("price")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        out[str(key)] = str(value)
    return out or None


def _reason_from_wire(raw: Any) -> ModelUnavailableReason | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("unavailable_reason")
    code = raw.get("code")
    protocol = raw.get("required_protocol")
    if code not in _UNAVAILABLE_CODES or protocol not in _PROTOCOLS:
        raise ValueError("unavailable_reason")
    return ModelUnavailableReason(
        code=code,  # type: ignore[arg-type]
        required_protocol=protocol,  # type: ignore[arg-type]
    )


def _effort_from_wire(raw: Any) -> CatalogReasoningEffort | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("reasoning_effort")
    options = raw.get("options")
    if not isinstance(options, list) or not options:
        raise ValueError("reasoning_effort")
    return CatalogReasoningEffort(
        options=tuple(str(item) for item in options),
        default=str(raw.get("default") or ""),
    )


async def load_turn_model_catalog(user_id: str) -> ModelCatalog:
    """Load the caller's catalog. Raises :class:`TurnCatalogUnavailableError` on failure.

    Ticketed sidecar: account HTTP only. Otherwise the local session.
    """
    from agentcore.db.sidecar_tickets import sidecar_narrow_tickets_bound

    if sidecar_narrow_tickets_bound():
        return await _load_via_account_ticket()

    uid = (user_id or "").strip()
    if not uid:
        logger.warning("model_catalog.load_failed", reason="no_user")
        raise TurnCatalogUnavailableError("no_user")

    from agentcore.db.base import async_session_factory
    from agentcore.llm.catalog import resolve_model_catalog

    async with async_session_factory() as session:
        return await resolve_model_catalog(session, uid)


async def require_turn_model_catalog(
    user_id: str, *, unavailable: str
) -> tuple[ModelCatalog | None, str]:
    """``(catalog, "")`` or ``(None, unavailable)`` when this turn cannot read one."""
    try:
        return await load_turn_model_catalog(user_id), ""
    except TurnCatalogUnavailableError:
        return None, unavailable


async def _load_via_account_ticket() -> ModelCatalog:
    from agentcore.account.credentials import (
        AccountCloudError,
        cloud_list_models,
        get_account_credentials,
    )

    creds = get_account_credentials()
    if creds is None:
        logger.warning("model_catalog.load_failed", reason="no_account_ticket")
        raise TurnCatalogUnavailableError("no_account_ticket")
    try:
        payload = await cloud_list_models(creds)
    except AccountCloudError:
        logger.warning("model_catalog.load_failed", reason="account_http")
        raise TurnCatalogUnavailableError("account_http") from None
    try:
        return catalog_from_wire(payload)
    except (KeyError, TypeError, ValueError):
        logger.warning("model_catalog.load_failed", reason="bad_payload")
        raise TurnCatalogUnavailableError("bad_payload") from None
