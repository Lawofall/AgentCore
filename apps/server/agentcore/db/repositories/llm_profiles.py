"""Repository for ``assemblies`` (装配), including the model columns."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import LlmModelProfile
from agentcore.db.repositories._base import _UNSET


class LlmModelProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, profile_id: str, *, user_id: str) -> LlmModelProfile | None:
        result = await self._session.execute(
            select(LlmModelProfile).where(
                LlmModelProfile.id == profile_id,
                LlmModelProfile.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, profile_id: str) -> LlmModelProfile | None:
        result = await self._session.execute(
            select(LlmModelProfile).where(LlmModelProfile.id == profile_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self, user_id: str, *, include_implicit: bool = False
    ) -> Sequence[LlmModelProfile]:
        stmt = select(LlmModelProfile).where(LlmModelProfile.user_id == user_id)
        if not include_implicit:
            stmt = stmt.where(LlmModelProfile.kind == "user")
        stmt = stmt.order_by(LlmModelProfile.created_at.asc())
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def create(
        self,
        *,
        user_id: str,
        name: str,
        kind: str = "user",
        main_origin: str,
        main_model: str,
        main_provider_id: str | None = None,
        worker_origin: str | None = None,
        worker_provider_id: str | None = None,
        worker_model: str | None = None,
        background_origin: str | None = None,
        background_provider_id: str | None = None,
        background_model: str | None = None,
        vision_origin: str | None = None,
        vision_provider_id: str | None = None,
        vision_model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> LlmModelProfile:
        row = LlmModelProfile(
            user_id=user_id,
            name=name,
            kind=kind,
            main_origin=main_origin,
            main_provider_id=main_provider_id,
            main_model=main_model,
            worker_origin=worker_origin,
            worker_provider_id=worker_provider_id,
            worker_model=worker_model,
            background_origin=background_origin,
            background_provider_id=background_provider_id,
            background_model=background_model,
            vision_origin=vision_origin,
            vision_provider_id=vision_provider_id,
            vision_model=vision_model,
            reasoning_effort=reasoning_effort,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def update(
        self,
        profile_id: str,
        *,
        user_id: str,
        name: str | object = _UNSET,
        enabled_mcp_server_ids: list | object = _UNSET,
        omit_factory_catalog: bool | object = _UNSET,
        recipe: str | None | object = _UNSET,
        omit_desk_rules: bool | object = _UNSET,
        main_origin: str | object = _UNSET,
        main_provider_id: str | None | object = _UNSET,
        main_model: str | object = _UNSET,
        worker_origin: str | None | object = _UNSET,
        worker_provider_id: str | None | object = _UNSET,
        worker_model: str | None | object = _UNSET,
        background_origin: str | None | object = _UNSET,
        background_provider_id: str | None | object = _UNSET,
        background_model: str | None | object = _UNSET,
        vision_origin: str | None | object = _UNSET,
        vision_provider_id: str | None | object = _UNSET,
        vision_model: str | None | object = _UNSET,
        reasoning_effort: str | None | object = _UNSET,
        context_budget: int | None | object = _UNSET,
    ) -> LlmModelProfile | None:
        row = await self.get(profile_id, user_id=user_id)
        if row is None:
            return None
        if name is not _UNSET:
            row.name = str(name)
        if enabled_mcp_server_ids is not _UNSET:
            row.enabled_mcp_server_ids = list(cast(list[object], enabled_mcp_server_ids))
        if omit_factory_catalog is not _UNSET:
            row.omit_factory_catalog = bool(omit_factory_catalog)
        if recipe is not _UNSET:
            row.recipe = None if recipe is None else str(recipe)
        if omit_desk_rules is not _UNSET:
            row.omit_desk_rules = bool(omit_desk_rules)
        if main_origin is not _UNSET:
            row.main_origin = str(main_origin)
        if main_provider_id is not _UNSET:
            row.main_provider_id = None if main_provider_id is None else str(main_provider_id)
        if main_model is not _UNSET:
            row.main_model = str(main_model)
        if worker_origin is not _UNSET:
            row.worker_origin = None if worker_origin is None else str(worker_origin)
        if worker_provider_id is not _UNSET:
            row.worker_provider_id = (
                None if worker_provider_id is None else str(worker_provider_id)
            )
        if worker_model is not _UNSET:
            row.worker_model = None if worker_model is None else str(worker_model)
        if background_origin is not _UNSET:
            row.background_origin = (
                None if background_origin is None else str(background_origin)
            )
        if background_provider_id is not _UNSET:
            row.background_provider_id = (
                None if background_provider_id is None else str(background_provider_id)
            )
        if background_model is not _UNSET:
            row.background_model = (
                None if background_model is None else str(background_model)
            )
        if vision_origin is not _UNSET:
            row.vision_origin = None if vision_origin is None else str(vision_origin)
        if vision_provider_id is not _UNSET:
            row.vision_provider_id = (
                None if vision_provider_id is None else str(vision_provider_id)
            )
        if vision_model is not _UNSET:
            row.vision_model = None if vision_model is None else str(vision_model)
        if reasoning_effort is not _UNSET:
            row.reasoning_effort = (
                None if reasoning_effort is None else str(reasoning_effort)
            )
        if context_budget is not _UNSET:
            row.context_budget = (
                None if context_budget is None else int(cast(int, context_budget))
            )
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete(self, profile_id: str, *, user_id: str) -> bool:
        result = await self._session.execute(
            delete(LlmModelProfile).where(
                LlmModelProfile.id == profile_id,
                LlmModelProfile.user_id == user_id,
            )
        )
        await self._session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0

    async def delete_all_for_user(self, user_id: str) -> int:
        result = await self._session.execute(
            delete(LlmModelProfile).where(LlmModelProfile.user_id == user_id)
        )
        await self._session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    async def clear_provider_refs(self, user_id: str, provider_id: str) -> None:
        """Clear worker / background / vision pins that reference a deleted BYOK provider."""
        rows = await self.list_for_user(user_id, include_implicit=True)
        changed = False
        for row in rows:
            if row.worker_provider_id == provider_id:
                row.worker_origin = None
                row.worker_provider_id = None
                row.worker_model = None
                changed = True
            if row.background_provider_id == provider_id:
                row.background_origin = None
                row.background_provider_id = None
                row.background_model = None
                changed = True
            if row.vision_provider_id == provider_id:
                row.vision_origin = None
                row.vision_provider_id = None
                row.vision_model = None
                changed = True
        if changed:
            await self._session.commit()

    async def retarget_main_provider(
        self,
        user_id: str,
        *,
        from_provider_id: str,
        to_provider_id: str | None,
        to_model: str | None,
        to_origin: str,
    ) -> None:
        """When a BYOK provider is deleted, retarget assemblies that used it as main."""
        values: dict[str, object | None] = {
            "main_provider_id": to_provider_id,
            "main_origin": to_origin,
        }
        if to_model is not None:
            values["main_model"] = to_model
        await self._session.execute(
            update(LlmModelProfile)
            .where(
                LlmModelProfile.user_id == user_id,
                LlmModelProfile.main_provider_id == from_provider_id,
            )
            .values(**values)
        )
        await self._session.commit()
