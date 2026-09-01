"""Model combination profiles (模型组合) — CRUD + expand as a **derived query layer**.

A profile is ``{main, worker?, background?, vision?}``. Empty worker / background =
follow_main. Empty vision does **not** persist follow_main into the slot columns.
VisionReader resolve may reuse main credentials when that id accepts images
(``llm.image_accept``); else platform ``VISION_*`` only when ``billing_mode=platform``.

**Not a model-metadata owner.** Platform 上架 / display enrichment live in
:mod:`agentcore.llm.catalog` (+ :mod:`agentcore.llm.model_metadata`). System presets
are virtual well-known ids **projected** from
:func:`agentcore.llm.catalog.platform_listable_model_ids` (recognition) /
:func:`agentcore.llm.catalog.visible_platform_listable_model_ids` (list / select).
Each listable platform model id → one system combo (main = that platform model;
worker / background / vision follow-null). Combo names come from
:func:`agentcore.llm.catalog.platform_model_label` — display name **plus** curated
badge, since a combo name is a lone string and the free / priced SKUs of one model
share a display name. Stable ids use
``uuid5(NAMESPACE_URL, "agentcore:platform-preset:{model_id}")`` — no hardcoded
product UUID table.

Distinct from scenario ``ProfileParams`` (temperature / rounds) in ``llm/profiles.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.billing.preference import platform_catalog_visible
from agentcore.config import settings
from agentcore.core.errors import NotFoundError, ValidationError
from agentcore.db.models import LlmModelProfile
from agentcore.db.repositories import (
    LlmModelProfileRepository,
    UserLlmProviderRepository,
    UserRepository,
)
from agentcore.db.repositories._base import _UNSET
from agentcore.llm.profiles import PLATFORM_MODEL_FLASH
from agentcore.llm.resolve import ModelOrigin, ModelSelection

ProfileKind = Literal["system", "user", "implicit"]

_PRESET_NS = uuid.NAMESPACE_URL
_PRESET_PREFIX = "agentcore:platform-preset:"


@dataclass(frozen=True)
class ProfileSlot:
    origin: ModelOrigin
    model: str
    provider_id: str | None = None


@dataclass(frozen=True)
class ExpandedProfile:
    """Resolved slots after expand.

    Worker/background None = follow_main. Vision None = no dedicated slot (not a
    copied main); reader resolve may still follow an image-accepting main.
    """

    profile_id: str
    name: str
    kind: ProfileKind
    main: ModelSelection
    worker: ModelSelection | None = None
    background: ModelSelection | None = None
    vision: ModelSelection | None = None


@dataclass(frozen=True)
class ModelProfileView:
    id: str
    name: str
    kind: ProfileKind
    main: ProfileSlot
    worker: ProfileSlot | None = None
    background: ProfileSlot | None = None
    vision: ProfileSlot | None = None
    is_default: bool = False
    warnings: tuple[str, ...] = ()


def platform_preset_id(model_id: str) -> str:
    """Stable virtual system-preset id for a platform model id."""
    return str(uuid.uuid5(_PRESET_NS, f"{_PRESET_PREFIX}{model_id}"))


def system_presets() -> dict[str, str]:
    """profile_id → platform model id, projected from catalog 上架 ids.

    Recognition map includes dormant (gate-off) listable ids so a DB pin on a
    system preset still identifies as system. Listing / selection use
    :func:`_visible_system_ids` instead.

    Late-imports catalog so tests can monkeypatch ``platform_listable_model_ids``.
    """
    from agentcore.llm.catalog import platform_listable_model_ids

    return {platform_preset_id(mid): mid for mid in platform_listable_model_ids()}


def system_profile_default_id() -> str | None:
    """Logical default preset: ``PLATFORM_MODEL`` if listable, else first system preset."""
    presets = system_presets()
    if not presets:
        return None
    platform_model = (settings.platform_model or "").strip() or PLATFORM_MODEL_FLASH
    for pid, mid in presets.items():
        if mid == platform_model:
            return pid
    return next(iter(presets))


def is_system_profile_id(profile_id: str | None) -> bool:
    return bool(profile_id) and profile_id in system_presets()


def _system_preset_display_name(model_id: str) -> str:
    from agentcore.llm.catalog import platform_model_label

    return platform_model_label(model_id)


def _visible_system_ids() -> list[str]:
    """System preset ids currently listable (= catalog visible 上架 projection)."""
    from agentcore.llm.catalog import visible_platform_listable_model_ids

    return [platform_preset_id(mid) for mid in visible_platform_listable_model_ids()]


def _system_preset_available(profile_id: str) -> bool:
    """True when this system preset may appear in list / be selected.

    Derived solely from catalog's visible 上架 set (no parallel gate).
    """
    return profile_id in set(_visible_system_ids())


def resolve_system_preset_main(profile_id: str) -> ModelSelection:
    """Fixed platform model for a system preset (no keyword ranking)."""
    model_id = system_presets()[profile_id]
    return ModelSelection(model=model_id, origin="platform", provider_id=None)


def _slot_from_row(
    origin: str | None, model: str | None, provider_id: str | None
) -> ProfileSlot | None:
    model_s = (model or "").strip() or None
    if not model_s:
        return None
    origin_s: ModelOrigin = "platform" if origin == "platform" else "byok"
    return ProfileSlot(
        origin=origin_s,
        model=model_s,
        provider_id=provider_id if origin_s == "byok" else None,
    )


async def _provider_first_fallback(
    session: AsyncSession, user_id: str
) -> ModelSelection:
    """BYOK first provider / keyless platform — no profile expand (avoids recursion)."""
    from agentcore.llm.resolve import _default_chat_provider_row

    row = await _default_chat_provider_row(session, user_id)
    if row is not None:
        model = (row.default_model or "").strip() or PLATFORM_MODEL_FLASH
        return ModelSelection(model=model, origin="byok", provider_id=row.id)
    platform_model = (settings.platform_model or "").strip() or PLATFORM_MODEL_FLASH
    origin: ModelOrigin = "platform" if platform_catalog_visible() else "byok"
    return ModelSelection(model=platform_model, origin=origin, provider_id=None)


async def _live_selection(
    session: AsyncSession,
    user_id: str,
    slot: ProfileSlot,
) -> ModelSelection:
    """Validate a stored slot against live providers / platform gate; silent fallback."""
    from agentcore.llm.resolve import _default_chat_provider_row, _load_provider

    if slot.origin == "platform":
        if not platform_catalog_visible():
            return await _provider_first_fallback(session, user_id)
        return ModelSelection(model=slot.model, origin="platform", provider_id=None)

    if slot.provider_id:
        row = await _load_provider(session, user_id, slot.provider_id)
        if row is not None:
            return ModelSelection(
                model=slot.model, origin="byok", provider_id=row.id
            )
        return await _provider_first_fallback(session, user_id)

    row = await _default_chat_provider_row(session, user_id)
    if row is not None:
        return ModelSelection(model=slot.model, origin="byok", provider_id=row.id)
    return await _provider_first_fallback(session, user_id)


class LlmModelProfileService:
    """CRUD + default + expand for model combination profiles (derived query layer)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = LlmModelProfileRepository(session)
        self._users = UserRepository(session)
        self._providers = UserLlmProviderRepository(session)

    async def _default_id(self, user_id: str) -> str | None:
        user = await self._users.get_by_id(user_id)
        if user is None:
            return None
        return getattr(user, "default_model_profile_id", None)

    def _view_system(self, profile_id: str, *, is_default: bool) -> ModelProfileView:
        model_id = system_presets()[profile_id]
        main = resolve_system_preset_main(profile_id)
        return ModelProfileView(
            id=profile_id,
            name=_system_preset_display_name(model_id),
            kind="system",
            main=ProfileSlot(
                origin=main.origin, model=main.model, provider_id=main.provider_id
            ),
            worker=None,
            background=None,
            vision=None,
            is_default=is_default,
        )

    def _view_row(self, row: LlmModelProfile, *, is_default: bool) -> ModelProfileView:
        main = _slot_from_row(row.main_origin, row.main_model, row.main_provider_id)
        assert main is not None  # DB requires main_model
        return ModelProfileView(
            id=row.id,
            name=row.name,
            kind=row.kind if row.kind in ("user", "implicit") else "user",  # type: ignore[arg-type]
            main=main,
            worker=_slot_from_row(
                row.worker_origin, row.worker_model, row.worker_provider_id
            ),
            background=_slot_from_row(
                row.background_origin, row.background_model, row.background_provider_id
            ),
            vision=_slot_from_row(
                row.vision_origin, row.vision_model, row.vision_provider_id
            ),
            is_default=is_default,
        )

    def _visible_system_ids(self) -> list[str]:
        return _visible_system_ids()

    def _mark_default(
        self, views: list[ModelProfileView], default_id: str | None
    ) -> list[ModelProfileView]:
        known = {v.id for v in views}
        # Invisible pin (e.g. system preset while platform dormant) → logical
        # default only; never rewrite DB.
        effective = default_id if default_id in known else None
        if effective is None:
            logical = system_profile_default_id()
            if logical is not None and logical in known:
                effective = logical
            else:
                effective = next((v.id for v in views if v.kind == "system"), None)
            if effective is None:
                effective = next((v.id for v in views), None)
        return [
            ModelProfileView(
                id=v.id,
                name=v.name,
                kind=v.kind,
                main=v.main,
                worker=v.worker,
                background=v.background,
                vision=v.vision,
                is_default=(v.id == effective),
                warnings=v.warnings,
            )
            for v in views
        ]

    async def list_profiles(self, user_id: str) -> list[ModelProfileView]:
        default_id = await self._default_id(user_id)
        views = [
            self._view_system(pid, is_default=False)
            for pid in self._visible_system_ids()
        ]
        for row in await self._repo.list_for_user(user_id, include_implicit=False):
            views.append(self._view_row(row, is_default=False))
        return self._mark_default(views, default_id)

    async def snapshot_default_profile_id(self, user_id: str) -> str | None:
        """Profile id to pin on a new conversation (account default / logical preset)."""
        for view in await self.list_profiles(user_id):
            if view.is_default:
                return view.id
        return None

    async def get_profile(self, user_id: str, profile_id: str) -> ModelProfileView:
        if is_system_profile_id(profile_id):
            if not _system_preset_available(profile_id):
                raise NotFoundError("模型组合不存在")
            for view in await self.list_profiles(user_id):
                if view.id == profile_id:
                    return view
            raise NotFoundError("模型组合不存在")
        default_id = await self._default_id(user_id)
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise NotFoundError("模型组合不存在")
        return self._view_row(row, is_default=(row.id == default_id))

    async def _validate_slot(
        self, user_id: str, slot: ProfileSlot, *, label: str
    ) -> None:
        if not (slot.model or "").strip():
            raise ValidationError(f"{label} 模型不能为空")
        if slot.origin == "platform":
            if slot.provider_id:
                raise ValidationError(f"{label} 平台模型不能指定服务商")
            if not platform_catalog_visible():
                raise ValidationError("当前部署不可用平台模型")
            from agentcore.llm.catalog import is_platform_listable

            if not is_platform_listable(slot.model):
                raise ValidationError(f"{label} 所选模型不在平台目录中")
            return
        if not slot.provider_id:
            raise ValidationError(f"{label} 自带 Key 模型须指定服务商")
        row = await self._providers.get(slot.provider_id, user_id=user_id)
        if row is None:
            raise ValidationError(f"{label} 所选服务商不存在")

    async def _byok_reachability_warnings(
        self,
        user_id: str,
        slots: list[tuple[str, ProfileSlot]],
    ) -> tuple[str, ...]:
        """Best-effort BYOK model warnings for save responses (never raises / blocks).

        Uses the same reachability ladder as connectivity test, with
        :data:`SAVE_WARN_POLICY` so list fetch failures stay silent.
        """
        by_provider: dict[str, list[tuple[str, str]]] = {}
        for label, slot in slots:
            if slot.origin != "byok" or not slot.provider_id:
                continue
            model_s = (slot.model or "").strip()
            if not model_s:
                continue
            by_provider.setdefault(slot.provider_id, []).append((label, model_s))
        if not by_provider:
            return ()

        from agentcore.llm.factory import build_provider
        from agentcore.llm.model_reachability import (
            SAVE_WARN_POLICY,
            check_model_reachable,
            fetch_model_list,
        )
        from agentcore.llm.resolve import resolve_provider_credentials

        warnings: list[str] = []
        try:
            for provider_id, items in by_provider.items():
                credentials = await resolve_provider_credentials(
                    self._session, user_id, provider_id
                )
                if credentials is None:
                    continue
                provider = build_provider(credentials)
                try:
                    model_list = await fetch_model_list(provider)
                    seen: set[str] = set()
                    for label, model_s in items:
                        if model_s in seen:
                            continue
                        seen.add(model_s)
                        reach, detail = await check_model_reachable(
                            provider,
                            model=model_s,
                            model_list=model_list,
                            policy=SAVE_WARN_POLICY,
                        )
                        if reach != "error":
                            continue
                        suffix = f"：{detail}" if detail else ""
                        warnings.append(
                            f"{label} 模型「{model_s}」可能不可用{suffix}"
                        )
                finally:
                    await provider.close()
        except Exception:  # noqa: BLE001 — save must not fail on warn checks
            return tuple(warnings)
        return tuple(warnings)

    async def create_profile(
        self,
        user_id: str,
        *,
        name: str,
        main: ProfileSlot,
        worker: ProfileSlot | None = None,
        background: ProfileSlot | None = None,
        vision: ProfileSlot | None = None,
        kind: str = "user",
        set_as_default: bool = False,
    ) -> ModelProfileView:
        name_s = (name or "").strip()
        if not name_s:
            raise ValidationError("组合名称不能为空")
        await self._validate_slot(user_id, main, label="main")
        if worker is not None:
            await self._validate_slot(user_id, worker, label="worker")
        if background is not None:
            await self._validate_slot(user_id, background, label="background")
        if vision is not None:
            await self._validate_slot(user_id, vision, label="vision")

        row = await self._repo.create(
            user_id=user_id,
            name=name_s,
            kind=kind,
            main_origin=main.origin,
            main_provider_id=main.provider_id if main.origin == "byok" else None,
            main_model=main.model.strip(),
            worker_origin=worker.origin if worker else None,
            worker_provider_id=(
                worker.provider_id if worker and worker.origin == "byok" else None
            ),
            worker_model=worker.model.strip() if worker else None,
            background_origin=background.origin if background else None,
            background_provider_id=(
                background.provider_id
                if background and background.origin == "byok"
                else None
            ),
            background_model=background.model.strip() if background else None,
            vision_origin=vision.origin if vision else None,
            vision_provider_id=(
                vision.provider_id if vision and vision.origin == "byok" else None
            ),
            vision_model=vision.model.strip() if vision else None,
        )
        if set_as_default:
            await self._users.set_default_model_profile(user_id, row.id)
        warn_slots: list[tuple[str, ProfileSlot]] = [("main", main)]
        if worker is not None:
            warn_slots.append(("worker", worker))
        if background is not None:
            warn_slots.append(("background", background))
        if vision is not None:
            warn_slots.append(("vision", vision))
        warnings = await self._byok_reachability_warnings(user_id, warn_slots)
        view = self._view_row(row, is_default=set_as_default)
        if not warnings:
            return view
        return ModelProfileView(
            id=view.id,
            name=view.name,
            kind=view.kind,
            main=view.main,
            worker=view.worker,
            background=view.background,
            vision=view.vision,
            is_default=view.is_default,
            warnings=warnings,
        )

    async def update_profile(
        self,
        user_id: str,
        profile_id: str,
        *,
        name: str | None = None,
        main: ProfileSlot | None = None,
        worker: ProfileSlot | None | object = _UNSET,
        background: ProfileSlot | None | object = _UNSET,
        vision: ProfileSlot | None | object = _UNSET,
        fields_set: set[str],
    ) -> ModelProfileView:
        if is_system_profile_id(profile_id):
            raise ValidationError("系统预置组合不可编辑")
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise NotFoundError("模型组合不存在")
        if row.kind == "implicit":
            raise ValidationError("隐式组合不可编辑，请新建用户组合")

        kwargs: dict = {}
        if "name" in fields_set and name is not None:
            name_s = name.strip()
            if not name_s:
                raise ValidationError("组合名称不能为空")
            kwargs["name"] = name_s
        if "main" in fields_set:
            if main is None:
                raise ValidationError("main 不能为空")
            await self._validate_slot(user_id, main, label="main")
            kwargs["main_origin"] = main.origin
            kwargs["main_provider_id"] = (
                main.provider_id if main.origin == "byok" else None
            )
            kwargs["main_model"] = main.model.strip()
        if "worker" in fields_set:
            if worker is None:
                kwargs["worker_origin"] = None
                kwargs["worker_provider_id"] = None
                kwargs["worker_model"] = None
            else:
                assert isinstance(worker, ProfileSlot)
                await self._validate_slot(user_id, worker, label="worker")
                kwargs["worker_origin"] = worker.origin
                kwargs["worker_provider_id"] = (
                    worker.provider_id if worker.origin == "byok" else None
                )
                kwargs["worker_model"] = worker.model.strip()
        if "background" in fields_set:
            if background is None:
                kwargs["background_origin"] = None
                kwargs["background_provider_id"] = None
                kwargs["background_model"] = None
            else:
                assert isinstance(background, ProfileSlot)
                await self._validate_slot(user_id, background, label="background")
                kwargs["background_origin"] = background.origin
                kwargs["background_provider_id"] = (
                    background.provider_id if background.origin == "byok" else None
                )
                kwargs["background_model"] = background.model.strip()
        if "vision" in fields_set:
            if vision is None:
                kwargs["vision_origin"] = None
                kwargs["vision_provider_id"] = None
                kwargs["vision_model"] = None
            else:
                assert isinstance(vision, ProfileSlot)
                await self._validate_slot(user_id, vision, label="vision")
                kwargs["vision_origin"] = vision.origin
                kwargs["vision_provider_id"] = (
                    vision.provider_id if vision.origin == "byok" else None
                )
                kwargs["vision_model"] = vision.model.strip()

        updated = await self._repo.update(profile_id, user_id=user_id, **kwargs)
        assert updated is not None
        default_id = await self._default_id(user_id)
        view = self._view_row(updated, is_default=(updated.id == default_id))

        slot_touched = bool(fields_set & {"main", "worker", "background", "vision"})
        if not slot_touched:
            return view
        warn_slots: list[tuple[str, ProfileSlot]] = [("main", view.main)]
        if view.worker is not None:
            warn_slots.append(("worker", view.worker))
        if view.background is not None:
            warn_slots.append(("background", view.background))
        if view.vision is not None:
            warn_slots.append(("vision", view.vision))
        warnings = await self._byok_reachability_warnings(user_id, warn_slots)
        if not warnings:
            return view
        return ModelProfileView(
            id=view.id,
            name=view.name,
            kind=view.kind,
            main=view.main,
            worker=view.worker,
            background=view.background,
            vision=view.vision,
            is_default=view.is_default,
            warnings=warnings,
        )

    async def delete_profile(self, user_id: str, profile_id: str) -> None:
        if is_system_profile_id(profile_id):
            raise ValidationError("系统预置组合不可删除")
        default_id = await self._default_id(user_id)
        if default_id == profile_id:
            raise ValidationError("不能删除账号默认组合，请先切换默认")
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise NotFoundError("模型组合不存在")
        # Conversations pinned here re-pin to account default (snapshot), not live NULL.
        from agentcore.db.repositories import ConversationRepository

        fallback = default_id or system_profile_default_id()
        await ConversationRepository(self._session).reassign_model_profile_refs(
            user_id, profile_id, to_profile_id=fallback
        )
        deleted = await self._repo.delete(profile_id, user_id=user_id)
        if not deleted:
            raise NotFoundError("模型组合不存在")

    async def set_default(self, user_id: str, profile_id: str) -> ModelProfileView:
        if is_system_profile_id(profile_id):
            if not _system_preset_available(profile_id):
                raise ValidationError("所选系统预置当前不可用")
            await self._users.set_default_model_profile(user_id, profile_id)
            return self._view_system(profile_id, is_default=True)
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise NotFoundError("模型组合不存在")
        if row.kind == "implicit":
            raise ValidationError("不能将隐式组合设为账号默认")
        await self._users.set_default_model_profile(user_id, profile_id)
        return self._view_row(row, is_default=True)

    async def ensure_profile_usable(self, user_id: str, profile_id: str) -> None:
        """Raise if ``profile_id`` is not a usable system preset and not owned by the user."""
        if is_system_profile_id(profile_id):
            if not _system_preset_available(profile_id):
                raise ValidationError("所选模型组合当前不可用")
            return
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise ValidationError("所选模型组合不存在或不属于你")

    async def _expand_logical_fallback(self, user_id: str) -> ExpandedProfile:
        """Visible BYOK combo or provider-first; name/origin match runtime (no DB write)."""
        from agentcore.llm.catalog import platform_model_label

        rows = await self._repo.list_for_user(user_id, include_implicit=False)
        if rows:
            return await self.expand(user_id, rows[0].id)
        main = await _provider_first_fallback(self._session, user_id)
        return ExpandedProfile(
            profile_id=main.provider_id or "",
            name=platform_model_label(main.model),
            kind="implicit",
            main=main,
            worker=None,
            background=None,
            vision=None,
        )

    async def expand(
        self,
        user_id: str,
        profile_id: str | None,
    ) -> ExpandedProfile:
        """Expand a profile id (or account default / platform preset) into live selections."""
        logical_default = system_profile_default_id()
        effective = profile_id or await self._default_id(user_id) or logical_default

        if effective and is_system_profile_id(effective):
            if not _system_preset_available(effective):
                if (
                    logical_default
                    and effective != logical_default
                    and _system_preset_available(logical_default)
                ):
                    return await self.expand(user_id, logical_default)
                # Dormant / missing from allowlist — logical fallback, keep DB pin.
                return await self._expand_logical_fallback(user_id)
            model_id = system_presets()[effective]
            name = _system_preset_display_name(model_id)
            main = resolve_system_preset_main(effective)
            return ExpandedProfile(
                profile_id=effective,
                name=name,
                kind="system",
                main=main,
                worker=None,
                background=None,
                vision=None,
            )

        if not effective:
            return await self._expand_logical_fallback(user_id)

        row = await self._repo.get(effective, user_id=user_id)
        if row is None:
            # Dangling default / conversation pin / retired virtual id → logical default.
            if logical_default and _system_preset_available(logical_default):
                return await self.expand(user_id, logical_default)
            return await self._expand_logical_fallback(user_id)

        main_slot = _slot_from_row(row.main_origin, row.main_model, row.main_provider_id)
        assert main_slot is not None
        main = await _live_selection(self._session, user_id, main_slot)

        worker_slot = _slot_from_row(
            row.worker_origin, row.worker_model, row.worker_provider_id
        )
        worker = (
            await _live_selection(self._session, user_id, worker_slot)
            if worker_slot
            else None
        )

        bg_slot = _slot_from_row(
            row.background_origin, row.background_model, row.background_provider_id
        )
        background = (
            await _live_selection(self._session, user_id, bg_slot) if bg_slot else None
        )

        vision_slot = _slot_from_row(
            row.vision_origin, row.vision_model, row.vision_provider_id
        )
        vision = (
            await _live_selection(self._session, user_id, vision_slot)
            if vision_slot
            else None
        )

        return ExpandedProfile(
            profile_id=row.id,
            name=row.name,
            kind="implicit" if row.kind == "implicit" else "user",
            main=main,
            worker=worker,
            background=background,
            vision=vision,
        )

    async def expand_for_conversation(
        self, user_id: str, conv
    ) -> ExpandedProfile:
        profile_id = getattr(conv, "model_profile_id", None) or None
        return await self.expand(user_id, profile_id)
