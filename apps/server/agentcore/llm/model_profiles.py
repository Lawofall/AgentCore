"""Model combination profiles (模型组合) — CRUD + expand as a **derived query layer**.

A profile is ``{main, worker?}`` plus optional vendor ``reasoning_effort``
and optional ``context_budget`` (NULL = model window). Empty worker =
follow_main. Images ride ``main``.

**Not a model-metadata owner.** Platform 上架 / display enrichment live in
:mod:`agentcore.llm.catalog` (+ :mod:`agentcore.llm.model_metadata`). The official
list is the locked 完整 recipe; its id is
``uuid5(NAMESPACE_URL, "agentcore:capability-preset:full")``. It uses the
platform default model (or the first visible 上架 id).

Distinct from scenario ``ProfileParams`` (temperature / rounds) in ``llm/profiles.py``.
"""

from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass, replace
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
from agentcore.llm.byok_provider_presets import seed_model_for_vendor
from agentcore.llm.context_budget import normalize_context_budget, snap_context_budget
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
    """Resolved slots after expand. Worker None = follow_main."""

    profile_id: str
    name: str
    kind: ProfileKind
    main: ModelSelection
    worker: ModelSelection | None = None
    reasoning_effort: str | None = None
    # Shorter than the main model's window. None = that model's catalog window.
    context_budget: int | None = None


@dataclass(frozen=True)
class ModelProfileView:
    """One assembly, including its model columns."""

    id: str
    name: str
    kind: ProfileKind
    is_default: bool = False
    # full while an owned row still locks official 完整. None on snapshots.
    recipe: str | None = None
    warnings: tuple[str, ...] = ()
    enabled_mcp_server_ids: tuple[str, ...] = ()
    omitted_factory_skills: tuple[str, ...] = ()
    main: ProfileSlot | None = None
    worker: ProfileSlot | None = None
    reasoning_effort: str | None = None
    context_budget: int | None = None


def _locked_recipe_key(row: object) -> str | None:
    """Recipe key still on this owned row, or None once it has been released."""
    from agentcore.assembly.recipes import capability_recipe_for_key

    recipe = capability_recipe_for_key(getattr(row, "recipe", None))
    if recipe is None:
        return None
    return recipe.key


def _mcp_server_ids(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        server_id = str(item).strip()
        if not server_id or server_id in seen:
            continue
        seen.add(server_id)
        out.append(server_id)
    return tuple(out)


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


def _capability_main_model_id() -> str | None:
    """Platform default when it is visible, else the first visible 上架 id."""
    from agentcore.llm.catalog import visible_platform_listable_model_ids

    visible = list(visible_platform_listable_model_ids())
    if not visible:
        return None
    platform_model = (settings.platform_model or "").strip() or PLATFORM_MODEL_FLASH
    if platform_model in visible:
        return platform_model
    return visible[0]


def system_profile_default_id() -> str:
    """Logical default assembly: the 完整 recipe. Its model is copied on materialize."""
    from agentcore.assembly.recipes import FULL, capability_preset_id

    return capability_preset_id(FULL)


def is_system_profile_id(profile_id: str | None) -> bool:
    """True for an official capability recipe."""
    if not profile_id:
        return False
    from agentcore.assembly.recipes import capability_recipe_for_id

    return capability_recipe_for_id(profile_id) is not None


def _system_preset_display_name(model_id: str) -> str:
    from agentcore.llm.catalog import platform_model_display_name

    return platform_model_display_name(model_id)


def _visible_system_ids() -> list[str]:
    """Official recipes. Listing does not depend on a platform model."""
    from agentcore.assembly.recipes import CAPABILITY_ORDER, capability_preset_id

    return [capability_preset_id(key) for key in CAPABILITY_ORDER]


def _system_preset_available(profile_id: str) -> bool:
    """Official recipes stay selectable. Model availability is the slot's concern."""
    return is_system_profile_id(profile_id)


def _normalize_reasoning_effort(main_model: str, value: str | None) -> str | None:
    """Persist official vendor tokens only. Blank → None (vendor default)."""
    from agentcore.llm.provider.wire_dialect import reasoning_effort_spec

    raw = (value or "").strip() or None
    spec = reasoning_effort_spec(main_model)
    if raw is None:
        return None
    if spec is None:
        raise ValidationError("当前主模型不支持思考强度")
    options, _default = spec
    if raw not in options:
        raise ValidationError(f"思考强度须为该模型厂商档位之一：{', '.join(options)}")
    return raw


def _snap_reasoning_effort(main_model: str, stored: str | None) -> str | None:
    """Keep a stored token only if the (new) main model still lists it."""
    from agentcore.llm.provider.wire_dialect import reasoning_effort_spec

    raw = (stored or "").strip() or None
    if raw is None:
        return None
    spec = reasoning_effort_spec(main_model)
    if spec is None or raw not in spec[0]:
        return None
    return raw


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


def _slot_from_mapping(raw: object) -> ProfileSlot | None:
    if not isinstance(raw, dict):
        return None
    return _slot_from_row(
        raw.get("origin") if isinstance(raw.get("origin"), str) else None,
        raw.get("model") if isinstance(raw.get("model"), str) else None,
        raw.get("provider_id") if isinstance(raw.get("provider_id"), str) else None,
    )


async def _provider_first_fallback(session: AsyncSession, user_id: str) -> ModelSelection:
    """BYOK first provider / keyless platform — no profile expand (avoids recursion)."""
    from agentcore.llm.resolve import _default_chat_provider_row

    row = await _default_chat_provider_row(session, user_id)
    if row is not None:
        model = seed_model_for_vendor(row.vendor_id or "") or PLATFORM_MODEL_FLASH
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
            return ModelSelection(model=slot.model, origin="byok", provider_id=row.id)
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
        return getattr(user, "default_assembly_id", None)

    def _view_system(self, profile_id: str, *, is_default: bool) -> ModelProfileView:
        from agentcore.assembly.recipes import (
            capability_recipe_for_id,
            surface_from_recipe,
        )

        recipe = capability_recipe_for_id(profile_id)
        assert recipe is not None
        model_id = _capability_main_model_id()
        main = (
            ProfileSlot(origin="platform", model=model_id, provider_id=None) if model_id else None
        )
        return ModelProfileView(
            id=profile_id,
            name=recipe.name,
            kind="system",
            is_default=is_default,
            recipe=recipe.key,
            omitted_factory_skills=surface_from_recipe(recipe).omitted_factory_skills,
            main=main,
        )

    def _view_row(self, row: LlmModelProfile, *, is_default: bool) -> ModelProfileView:
        from agentcore.assembly.recipes import surface_from_row

        main = _slot_from_row(
            getattr(row, "main_origin", None),
            getattr(row, "main_model", None),
            getattr(row, "main_provider_id", None),
        )
        return ModelProfileView(
            id=row.id,
            name=row.name,
            kind=row.kind if row.kind in ("user", "implicit") else "user",  # type: ignore[arg-type]
            is_default=is_default,
            recipe=_locked_recipe_key(row),
            enabled_mcp_server_ids=_mcp_server_ids(getattr(row, "enabled_mcp_server_ids", None)),
            omitted_factory_skills=surface_from_row(row).omitted_factory_skills,
            main=main,
            worker=_slot_from_row(
                getattr(row, "worker_origin", None),
                getattr(row, "worker_model", None),
                getattr(row, "worker_provider_id", None),
            ),
            reasoning_effort=getattr(row, "reasoning_effort", None),
            context_budget=snap_context_budget(
                main.model if main is not None else "",
                getattr(row, "context_budget", None),
            ),
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
        return [replace(v, is_default=(v.id == effective)) for v in views]

    async def list_profiles(self, user_id: str) -> list[ModelProfileView]:
        default_id = await self._default_id(user_id)
        views = [
            self._view_system(pid, is_default=False) for pid in self._visible_system_ids()
        ]
        for row in await self._repo.list_for_user(user_id, include_implicit=False):
            views.append(self._view_row(row, is_default=False))
        return self._mark_default(views, default_id)

    async def snapshot_default_profile_id(self, user_id: str) -> str | None:
        """Id to pin on a new conversation. Official recipes stay virtual."""
        default_id = await self._default_id(user_id)
        if default_id and not is_system_profile_id(default_id):
            row = await self._repo.get(default_id, user_id=user_id)
            if row is not None:
                return row.id
        if default_id and is_system_profile_id(default_id):
            return default_id
        visible = self._preferred_visible_preset_ids()
        if visible:
            return visible[0]
        rows = await self._repo.list_for_user(user_id, include_implicit=False)
        if rows:
            await self._users.set_default_model_profile(user_id, rows[0].id)
            return rows[0].id
        return system_profile_default_id()

    async def ensure_writable_star(self, user_id: str) -> str | None:
        """Owned starred assembly. Official 完整 is copied first (点新建)."""
        pin = await self.snapshot_default_profile_id(user_id)
        if pin and not is_system_profile_id(pin):
            return pin
        from agentcore.assembly.recipes import (
            FULL,
            capability_preset_id,
            capability_recipe_for_id,
        )

        recipe = capability_recipe_for_id(capability_preset_id(FULL))
        name = f"{recipe.name} 副本" if recipe is not None else "完整 副本"
        view = await self.create_profile(
            user_id, name=name, origin="full", set_as_default=True
        )
        return view.id

    def _preferred_visible_preset_ids(self) -> list[str]:
        from agentcore.assembly.recipes import FULL, capability_preset_id

        preset_id = capability_preset_id(FULL)
        return [preset_id] if _system_preset_available(preset_id) else []

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

    async def _validate_slot(self, user_id: str, slot: ProfileSlot, *, label: str) -> None:
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
                        warnings.append(f"{label} 模型「{model_s}」可能不可用{suffix}")
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
        kind: str = "user",
        set_as_default: bool = False,
        origin: str = "full",
    ) -> ModelProfileView:
        name_s = (name or "").strip()
        if not name_s:
            raise ValidationError("能力名称不能为空")
        if origin != "full":
            raise ValidationError("新建从完整复制")
        model_id = _capability_main_model_id() or PLATFORM_MODEL_FLASH
        row = await self._repo.create(
            user_id=user_id,
            name=name_s,
            kind=kind,
            main_origin="platform",
            main_model=model_id,
        )
        from agentcore.assembly.membership import seed_assembly_skills_from_account

        await seed_assembly_skills_from_account(self._session, user_id, row.id)
        if set_as_default:
            await self._users.set_default_model_profile(user_id, row.id)
        return self._view_row(row, is_default=set_as_default)

    async def update_profile(
        self,
        user_id: str,
        profile_id: str,
        *,
        name: str | None = None,
        enabled_mcp_server_ids: list[str] | None = None,
        omitted_factory_skills: list[str] | None = None,
        main: ProfileSlot | None = None,
        worker: ProfileSlot | None | object = _UNSET,
        reasoning_effort: str | None | object = _UNSET,
        context_budget: int | None | object = _UNSET,
        fields_set: set[str],
    ) -> ModelProfileView:
        if is_system_profile_id(profile_id):
            from agentcore.assembly.recipes import OFFICIAL_READONLY

            raise ValidationError(OFFICIAL_READONLY)
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise NotFoundError("能力不存在")
        if row.kind == "implicit":
            raise ValidationError("隐式组合不可编辑，请新建用户组合")

        kwargs: dict = {}
        if "name" in fields_set and name is not None:
            name_s = name.strip()
            if not name_s:
                raise ValidationError("能力名称不能为空")
            kwargs["name"] = name_s
        if "enabled_mcp_server_ids" in fields_set:
            kwargs["enabled_mcp_server_ids"] = list(_mcp_server_ids(enabled_mcp_server_ids))
        if "omitted_factory_skills" in fields_set and omitted_factory_skills is not None:
            from agentcore.runtime.skills.factory_switches import (
                normalize_omitted_factory_skills,
            )

            kwargs["omitted_factory_skills"] = list(
                normalize_omitted_factory_skills(omitted_factory_skills)
            )
        if fields_set & {"enabled_mcp_server_ids", "omitted_factory_skills"}:
            kwargs["recipe"] = None
        if "main" in fields_set:
            if main is None:
                raise ValidationError("main 不能为空")
            await self._validate_slot(user_id, main, label="main")
            kwargs["main_origin"] = main.origin
            kwargs["main_provider_id"] = main.provider_id if main.origin == "byok" else None
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
        main_model_for_effort = kwargs.get("main_model") or getattr(row, "main_model", "")
        if "reasoning_effort" in fields_set:
            raw = None if reasoning_effort is None else str(reasoning_effort)
            kwargs["reasoning_effort"] = _normalize_reasoning_effort(
                str(main_model_for_effort), raw
            )
        elif "main" in fields_set:
            snapped = _snap_reasoning_effort(
                str(main_model_for_effort), getattr(row, "reasoning_effort", None)
            )
            if snapped != (getattr(row, "reasoning_effort", None) or None):
                kwargs["reasoning_effort"] = snapped
        if "context_budget" in fields_set:
            raw_budget = context_budget if isinstance(context_budget, int) else None
            kwargs["context_budget"] = normalize_context_budget(
                str(main_model_for_effort), raw_budget
            )
        elif "main" in fields_set:
            snapped_budget = snap_context_budget(
                str(main_model_for_effort), getattr(row, "context_budget", None)
            )
            stored_budget = getattr(row, "context_budget", None) or None
            if snapped_budget != stored_budget:
                kwargs["context_budget"] = snapped_budget

        updated = await self._repo.update(profile_id, user_id=user_id, **kwargs)
        assert updated is not None
        default_id = await self._default_id(user_id)
        view = self._view_row(updated, is_default=(updated.id == default_id))
        if fields_set & {"main", "worker"}:
            from agentcore.llm.model_visibility import byok_refs_from_slots

            maybe = self._users.enable_picker_refs(
                user_id,
                byok_refs_from_slots(view.main, view.worker),
            )
            if inspect.isawaitable(maybe):
                await maybe
        if not (fields_set & {"main", "worker"}):
            return view
        warn_slots: list[tuple[str, ProfileSlot]] = []
        if view.main is not None:
            warn_slots.append(("main", view.main))
        if view.worker is not None:
            warn_slots.append(("worker", view.worker))
        warnings = await self._byok_reachability_warnings(user_id, warn_slots)
        if not warnings:
            return view
        return replace(view, warnings=warnings)

    async def delete_profile(self, user_id: str, profile_id: str) -> None:
        """Drop an assembly. A starred one moves the star first.

        Official recipes cannot be removed. An owned row, including the star,
        is deleted. Conversations pinned here move to whatever is starred afterward.
        """
        if is_system_profile_id(profile_id):
            raise ValidationError("官方能力不能删除")
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise NotFoundError("模型组合不存在")
        await self._repoint_pins(user_id, profile_id)
        deleted = await self._repo.delete(profile_id, user_id=user_id)
        if not deleted:
            raise NotFoundError("模型组合不存在")

    async def _replacement_star(self, user_id: str, *, excluding: str) -> str | None:
        """Next star after ``excluding`` leaves: another owned row, else a tray recipe."""
        for row in await self._repo.list_for_user(user_id, include_implicit=False):
            if row.id != excluding and row.kind == "user":
                return row.id
        for preset_id in self._preferred_visible_preset_ids():
            if preset_id == excluding:
                continue
            return preset_id
        return None

    async def _repoint_pins(self, user_id: str, profile_id: str) -> None:
        """Move the star off ``profile_id`` when it is the star, then retarget chats."""
        from agentcore.db.repositories import ConversationRepository

        default_id = await self._default_id(user_id)
        if default_id == profile_id:
            fallback = await self._replacement_star(user_id, excluding=profile_id)
            await self._users.set_default_model_profile(user_id, fallback)
        else:
            fallback = default_id
            if fallback is None:
                fallback = await self._replacement_star(user_id, excluding=profile_id)
        if fallback == profile_id:
            fallback = None
        await ConversationRepository(self._session).reassign_model_profile_refs(
            user_id, profile_id, to_profile_id=fallback
        )

    async def set_default(self, user_id: str, profile_id: str) -> ModelProfileView:
        if is_system_profile_id(profile_id):
            await self._users.set_default_model_profile(user_id, profile_id)
            return self._view_system(profile_id, is_default=True)
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise NotFoundError("模型组合不存在")
        if row.kind == "implicit":
            raise ValidationError("不能将隐式组合设为账号默认")
        await self._users.set_default_model_profile(user_id, profile_id)
        return self._view_row(row, is_default=True)

    async def ensure_profile_usable(self, user_id: str, profile_id: str) -> str:
        """Return an assembly id that this account may pin. Official recipes stay virtual."""
        if is_system_profile_id(profile_id):
            return profile_id
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise ValidationError("所选能力不存在或不属于你")
        return profile_id

    async def _expand_row(self, user_id: str, row: LlmModelProfile) -> ExpandedProfile:
        main_slot = _slot_from_row(row.main_origin, row.main_model, row.main_provider_id)
        assert main_slot is not None
        main = await _live_selection(self._session, user_id, main_slot)
        worker_slot = _slot_from_row(row.worker_origin, row.worker_model, row.worker_provider_id)
        worker = await _live_selection(self._session, user_id, worker_slot) if worker_slot else None
        kind: ProfileKind = "implicit" if row.kind == "implicit" else "user"
        return ExpandedProfile(
            profile_id=row.id,
            name=row.name,
            kind=kind,
            main=main,
            worker=worker,
            reasoning_effort=_snap_reasoning_effort(
                main.model, getattr(row, "reasoning_effort", None)
            ),
            context_budget=snap_context_budget(main.model, getattr(row, "context_budget", None)),
        )

    async def _expand_platform_default(
        self, user_id: str, profile_id: str | None
    ) -> ExpandedProfile:
        model_id = _capability_main_model_id()
        if model_id is None or not platform_catalog_visible():
            return await self._expand_logical_fallback(user_id)
        from agentcore.assembly.recipes import capability_recipe_for_id

        effective = (
            profile_id
            if profile_id and is_system_profile_id(profile_id)
            else system_profile_default_id()
        )
        recipe = capability_recipe_for_id(effective)
        return ExpandedProfile(
            profile_id=effective,
            name=recipe.name if recipe is not None else "完整",
            kind="system",
            main=ModelSelection(model=model_id, origin="platform", provider_id=None),
        )

    async def _expand_logical_fallback(self, user_id: str) -> ExpandedProfile:
        from agentcore.llm.catalog import platform_model_label

        main = await _provider_first_fallback(self._session, user_id)
        return ExpandedProfile(
            profile_id=main.provider_id or "",
            name=platform_model_label(main.model),
            kind="implicit",
            main=main,
        )

    async def expand(
        self,
        user_id: str,
        profile_id: str | None,
    ) -> ExpandedProfile:
        """Live model selection from an assembly id, or the account star."""
        effective = profile_id or await self._default_id(user_id)
        if effective and not is_system_profile_id(effective):
            row = await self._repo.get(effective, user_id=user_id)
            if row is not None and (getattr(row, "main_model", None) or "").strip():
                return await self._expand_row(user_id, row)
        return await self._expand_platform_default(user_id, effective)

    async def expand_for_conversation(self, user_id: str, conv) -> ExpandedProfile:
        assembly_id = getattr(conv, "assembly_id", None) or None
        base = await self.expand(user_id, assembly_id)
        return await self._overlay_conversation_model(user_id, base, conv)

    async def normalize_conversation_override(self, user_id: str, override) -> dict:
        """Validate a conversation model write and return the stored JSON."""
        main = ProfileSlot(
            origin=override.main.origin,
            model=override.main.model,
            provider_id=override.main.provider_id,
        )
        await self._validate_slot(user_id, main, label="main")
        worker = None
        if override.worker is not None:
            worker = ProfileSlot(
                origin=override.worker.origin,
                model=override.worker.model,
                provider_id=override.worker.provider_id,
            )
            await self._validate_slot(user_id, worker, label="worker")
        effort = _normalize_reasoning_effort(main.model, override.reasoning_effort)
        budget = normalize_context_budget(main.model, override.context_budget)
        return {
            "main": {
                "origin": main.origin,
                "provider_id": main.provider_id,
                "model": main.model,
            },
            "worker": (
                {
                    "origin": worker.origin,
                    "provider_id": worker.provider_id,
                    "model": worker.model,
                }
                if worker is not None
                else None
            ),
            "reasoning_effort": effort,
            "context_budget": budget,
        }

    async def _overlay_conversation_model(
        self, user_id: str, base: ExpandedProfile, conv
    ) -> ExpandedProfile:
        raw = getattr(conv, "model_override", None)
        main_slot = _slot_from_mapping(raw.get("main") if isinstance(raw, dict) else None)
        if main_slot is None:
            return base
        main = await _live_selection(self._session, user_id, main_slot)
        worker_slot = _slot_from_mapping(raw.get("worker") if isinstance(raw, dict) else None)
        worker = (
            await _live_selection(self._session, user_id, worker_slot)
            if worker_slot is not None
            else None
        )
        effort_raw = raw.get("reasoning_effort") if isinstance(raw, dict) else None
        budget_raw = raw.get("context_budget") if isinstance(raw, dict) else None
        effort = _snap_reasoning_effort(
            main.model, effort_raw if isinstance(effort_raw, str) else None
        )
        budget = snap_context_budget(
            main.model, budget_raw if isinstance(budget_raw, int) else None
        )
        return replace(
            base,
            main=main,
            worker=worker,
            reasoning_effort=effort,
            context_budget=budget,
        )

    async def expand_for_conversation_id(
        self, user_id: str, conversation_id: str | None
    ) -> ExpandedProfile:
        """Assembly pinned on that conversation, else the account star.

        The conversation row stays in this module: ``model_selection`` must not
        import ``agentcore.db``.
        """
        if conversation_id:
            from agentcore.db.repositories import ConversationRepository

            conv = await ConversationRepository(self._session).get_by_id_unscoped(conversation_id)
            if conv is not None and getattr(conv, "user_id", None) == user_id:
                return await self.expand_for_conversation(user_id, conv)
        return await self.expand(user_id, None)
