"""Model catalog (模型目录): unified BYOK + platform model list.

Backs composer pickers and combination slot editors. Picker visibility
(``hidden_refs``) is account-level and does not change inference.
All routes are scoped to the authenticated user ("me").
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db, get_user_repo
from agentcore.api.schemas import (
    ComposerModelKnobs,
    ConversationModelOverride,
    HiddenModelRefsUpdate,
    HiddenModelRefsView,
    ModelCatalogCurrent,
    ModelCatalogItem,
    ModelCatalogResponse,
    ModelDisplayNameUpdate,
    ModelDisplayNameView,
    ModelPriceCard,
    ModelReasoningEffort,
    ModelUnavailableReason,
    VisionAcceptUpdate,
    VisionAcceptView,
)
from agentcore.db.repositories import UserRepository
from agentcore.llm.catalog import ModelCatalog, ModelCatalogEntry, resolve_model_catalog
from agentcore.llm.composer_knobs import (
    knobs_by_model,
    merge_composer_last_used,
    snapshot_without_knobs,
)
from agentcore.llm.image_accept import catalog_vision_tags
from agentcore.llm.model_display_names import (
    DisplayNameCapError,
    DisplayNameRefError,
    overlay_display_name,
)
from agentcore.llm.model_visibility import normalize_hidden_model_refs
from agentcore.llm.vision_declare import (
    VisionDeclareCapError,
    VisionDeclareRefError,
)

router = APIRouter(prefix="/users/me/models", tags=["models"])


async def _vision_accept_refs(users: object, user_id: str) -> tuple[str, ...]:
    getter = getattr(users, "vision_accept_refs", None)
    if getter is None:
        return ()
    return tuple(await getter(user_id))


def _unavailable_reason(item: ModelCatalogEntry) -> ModelUnavailableReason | None:
    reason = item.unavailable_reason
    if reason is None:
        return None
    return ModelUnavailableReason(
        code=reason.code,
        required_protocol=reason.required_protocol,
    )


def _composer_model(raw: dict | None) -> ConversationModelOverride | None:
    if not raw:
        return None
    try:
        return ConversationModelOverride.model_validate(raw)
    except (TypeError, ValueError):
        return None


def to_model_catalog_response(
    catalog: ModelCatalog,
    hidden_refs: list[str] | None = None,
    enabled_refs: list[str] | None = None,
    display_names: dict[str, str] | None = None,
    composer_model: dict | None = None,
    composer_model_knobs: dict | None = None,
    vision_accept_refs: list[str] | tuple[str, ...] | None = None,
) -> ModelCatalogResponse:
    names = display_names or {}
    declared = frozenset(vision_accept_refs or ())
    knobs = composer_model_knobs if composer_model_knobs is not None else knobs_by_model(
        composer_model
    )
    return ModelCatalogResponse(
        composer_model=_composer_model(snapshot_without_knobs(composer_model)),
        composer_model_knobs={
            ref: ComposerModelKnobs.model_validate(entry)
            for ref, entry in knobs.items()
        },
        current=ModelCatalogCurrent(
            id=catalog.current.id,
            origin=catalog.current.origin,
            provider_id=catalog.current.provider_id,
            ref=catalog.current.ref,
        ),
        byok_configured=catalog.byok_configured,
        hidden_refs=list(hidden_refs or ()),
        enabled_refs=list(enabled_refs or ()),
        models=[
            ModelCatalogItem(
                id=item.id,
                ref=item.ref,
                origin=item.origin,
                display_name=overlay_display_name(
                    origin=item.origin,
                    ref=item.ref,
                    display_name=item.display_name,
                    names=names,
                ),
                vendor=item.vendor,
                capabilities=catalog_vision_tags(
                    item.id,
                    item.capabilities,
                    declared=item.origin == "byok" and item.ref in declared,
                ),
                context_length=item.context_length,
                badge=item.badge,
                price=(
                    ModelPriceCard(
                        cache_hit=item.price.get("cache_hit"),
                        cache_miss=item.price.get("cache_miss"),
                        output=item.price.get("output"),
                        multiplier=item.price.get("multiplier") or "1",
                        quota_multiplier=item.price.get("quota_multiplier"),
                    )
                    if item.price
                    else None
                ),
                available=item.available,
                provider_id=item.provider_id,
                provider_label=item.provider_label,
                unavailable_reason=_unavailable_reason(item),
                reasoning_effort=(
                    ModelReasoningEffort(
                        options=list(item.reasoning_effort.options),
                        default=item.reasoning_effort.default,
                    )
                    if item.reasoning_effort
                    else None
                ),
                custom=item.custom,
            )
            for item in catalog.models
        ],
    )


_to_response = to_model_catalog_response


@router.get("", response_model=ModelCatalogResponse)
async def list_user_models(
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    users: UserRepository = Depends(get_user_repo),
) -> ModelCatalogResponse:
    """List the models this user may pick + the account's currently-resolved default."""
    hidden = await users.hidden_model_refs(user.user_id)
    enabled = await users.enabled_model_refs(user.user_id)
    catalog = await resolve_model_catalog(
        session, user.user_id, enabled_refs=enabled
    )
    names = await users.model_display_names(user.user_id)
    composer = await users.composer_model_override(user.user_id)
    vision_accept = await _vision_accept_refs(users, user.user_id)
    return to_model_catalog_response(
        catalog,
        hidden_refs=list(hidden),
        enabled_refs=list(enabled),
        display_names=names,
        composer_model=composer,
        composer_model_knobs=knobs_by_model(composer),
        vision_accept_refs=vision_accept,
    )


@router.put("/composer", response_model=ConversationModelOverride)
async def put_composer_model(
    body: ConversationModelOverride,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
    users: UserRepository = Depends(get_user_repo),
) -> ConversationModelOverride:
    """Replace the account last-used snapshot and upsert that main's knobs."""
    from agentcore.llm.model_profiles import LlmModelProfileService

    payload = await LlmModelProfileService(session).normalize_conversation_override(
        user.user_id, body
    )
    from agentcore.llm.model_visibility import byok_refs_from_override

    await users.enable_picker_refs(user.user_id, byok_refs_from_override(payload))
    existing = await users.composer_model_override(user.user_id)
    stored = await users.set_composer_model_override(
        user.user_id, merge_composer_last_used(existing, payload)
    )
    blob = stored if isinstance(stored, dict) else None
    parsed = _composer_model(snapshot_without_knobs(blob))
    if parsed is None:
        raise HTTPException(status_code=500, detail="保存模型选择失败")
    return parsed


@router.put("/hidden", response_model=HiddenModelRefsView)
async def put_hidden_model_refs(
    body: HiddenModelRefsUpdate,
    user: AuthUser,
    users: UserRepository = Depends(get_user_repo),
) -> HiddenModelRefsView:
    """Replace which catalog refs belong on composer pickers."""
    hidden, enabled = await users.set_picker_visibility(
        user.user_id,
        hidden=normalize_hidden_model_refs(body.refs),
        enabled=(
            None
            if body.enabled_refs is None
            else normalize_hidden_model_refs(body.enabled_refs)
        ),
    )
    return HiddenModelRefsView(refs=list(hidden), enabled_refs=list(enabled))


@router.put("/display-names", response_model=ModelDisplayNameView)
async def put_model_display_name(
    body: ModelDisplayNameUpdate,
    user: AuthUser,
    users: UserRepository = Depends(get_user_repo),
) -> ModelDisplayNameView:
    """Set or clear the shown name for one connected (BYOK) catalog row."""
    try:
        ref, name = await users.set_model_display_name(
            user.user_id, body.ref, body.display_name
        )
    except DisplayNameRefError as exc:
        if exc.code == "platform":
            raise HTTPException(status_code=400, detail="只能改自己接入的模型名。") from exc
        raise HTTPException(status_code=400, detail="不是有效的目录身份。") from exc
    except DisplayNameCapError as exc:
        raise HTTPException(status_code=400, detail="自定义名称过多。") from exc
    return ModelDisplayNameView(ref=ref, display_name=name)


@router.put("/vision-accept", response_model=VisionAcceptView)
async def put_vision_accept(
    body: VisionAcceptUpdate,
    user: AuthUser,
    users: UserRepository = Depends(get_user_repo),
) -> VisionAcceptView:
    """Declare or clear native image accept for one connected (BYOK) catalog row."""
    try:
        ref, accept = await users.set_vision_accept(
            user.user_id, body.ref, body.accept
        )
    except VisionDeclareRefError as exc:
        if exc.code == "platform":
            raise HTTPException(
                status_code=400, detail="只能声明自己接入的模型能看图。"
            ) from exc
        raise HTTPException(status_code=400, detail="不是有效的目录身份。") from exc
    except VisionDeclareCapError as exc:
        raise HTTPException(status_code=400, detail="声明的识图模型过多。") from exc
    return VisionAcceptView(ref=ref, accept=accept)
