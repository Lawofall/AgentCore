"""Model catalog (模型目录): unified BYOK + platform model list.

Backs **组合槽位编辑**（设置页选 main/worker/background 时列目录）；会话输入框只选
模型组合（``model_profile_id``），不再 PATCH 裸 ``conversations.model*``。
All routes are scoped to the authenticated user ("me").
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_db
from agentcore.api.schemas import (
    ModelCatalogCurrent,
    ModelCatalogItem,
    ModelCatalogResponse,
    ModelPriceCard,
    ModelUnavailableReason,
)
from agentcore.llm.catalog import ModelCatalog, ModelCatalogEntry, resolve_model_catalog

router = APIRouter(prefix="/users/me/models", tags=["models"])


def _unavailable_reason(item: ModelCatalogEntry) -> ModelUnavailableReason | None:
    reason = item.unavailable_reason
    if reason is None:
        return None
    return ModelUnavailableReason(
        code=reason.code,
        required_protocol=reason.required_protocol,
    )


def _to_response(catalog: ModelCatalog) -> ModelCatalogResponse:
    return ModelCatalogResponse(
        current=ModelCatalogCurrent(
            id=catalog.current.id,
            origin=catalog.current.origin,
            provider_id=catalog.current.provider_id,
        ),
        byok_configured=catalog.byok_configured,
        models=[
            ModelCatalogItem(
                id=item.id,
                origin=item.origin,
                display_name=item.display_name,
                vendor=item.vendor,
                capabilities=item.capabilities,
                context_length=item.context_length,
                badge=item.badge,
                price=ModelPriceCard(**item.price) if item.price else None,
                available=item.available,
                provider_id=item.provider_id,
                provider_label=item.provider_label,
                unavailable_reason=_unavailable_reason(item),
            )
            for item in catalog.models
        ],
    )


@router.get("", response_model=ModelCatalogResponse)
async def list_user_models(
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
) -> ModelCatalogResponse:
    """List the models this user may pick + the account's currently-resolved default."""
    return _to_response(await resolve_model_catalog(session, user.user_id))
