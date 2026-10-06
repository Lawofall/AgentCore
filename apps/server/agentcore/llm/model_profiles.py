"""Model combination profiles (模型组合) — CRUD + expand as a **derived query layer**.

A profile is ``{main, worker?, background?, vision?}`` plus optional vendor
``reasoning_effort`` and optional ``context_budget`` (NULL = model window).
Empty worker / background =
follow_main. The ``vision`` column is unused by live turns (images ride
``main``); CRUD still round-trips the field so existing rows / duplicate-profile
do not invent a second reader.

**Not a model-metadata owner.** Platform 上架 / display enrichment live in
:mod:`agentcore.llm.catalog` (+ :mod:`agentcore.llm.model_metadata`). The official
list is three capability recipes (极简 / 轻量 / 完整); ids are
``uuid5(NAMESPACE_URL, "agentcore:capability-preset:{chat|web|full}")``. Each uses
the platform default model (or the first visible 上架 id). Older
``uuid5(…, "agentcore:platform-preset:{model_id}")`` ids still resolve so a pin
from that projection can materialize, and they are not listed.

Distinct from scenario ``ProfileParams`` (temperature / rounds) in ``llm/profiles.py``.
"""

from __future__ import annotations

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
from agentcore.llm.byok_provider_presets import seed_model_for_base_url
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
    # chat | web | full while the official recipe is still locked. None once
    # tools, the envelope, the factory catalog, or plugs are edited.
    recipe: str | None = None
    warnings: tuple[str, ...] = ()
    enabled_mcp_server_ids: tuple[str, ...] = ()
    omit_factory_catalog: bool = False
    main: ProfileSlot | None = None
    worker: ProfileSlot | None = None
    background: ProfileSlot | None = None
    vision: ProfileSlot | None = None
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
    """True for an official capability recipe. Legacy per-model ids are slots."""
    if not profile_id:
        return False
    from agentcore.assembly.recipes import capability_recipe_for_id

    return capability_recipe_for_id(profile_id) is not None


def _recipe_key_hidden(profile_id: str, hidden: frozenset[str]) -> bool:
    from agentcore.assembly.recipes import capability_recipe_for_id

    recipe = capability_recipe_for_id(profile_id)
    return recipe is not None and recipe.key in hidden


def _system_preset_display_name(model_id: str) -> str:
    from agentcore.llm.catalog import platform_model_label

    return platform_model_label(model_id)


def _visible_system_ids() -> list[str]:
    """Official recipes. Listing does not depend on a platform model."""
    from agentcore.assembly.recipes import CAPABILITY_ORDER, capability_preset_id

    return [capability_preset_id(key) for key in CAPABILITY_ORDER]


def _system_preset_available(profile_id: str) -> bool:
    """Official recipes stay selectable. Model availability is the slot's concern."""
    return is_system_profile_id(profile_id)


def resolve_system_preset_main(profile_id: str) -> ModelSelection:
    """Fixed platform model for a system preset (no keyword ranking)."""
    model_id = system_presets()[profile_id]
    return ModelSelection(model=model_id, origin="platform", provider_id=None)


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


async def _provider_first_fallback(
    session: AsyncSession, user_id: str
) -> ModelSelection:
    """BYOK first provider / keyless platform — no profile expand (avoids recursion)."""
    from agentcore.llm.resolve import _default_chat_provider_row

    row = await _default_chat_provider_row(session, user_id)
    if row is not None:
        model = seed_model_for_base_url(row.base_url or "") or PLATFORM_MODEL_FLASH
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
        return getattr(user, "default_assembly_id", None)

    async def _hidden_recipe_keys(self, user_id: str) -> frozenset[str]:
        keys = await self._users.hidden_capability_recipe_keys(user_id)
        return frozenset(keys)

    def _view_system(self, profile_id: str, *, is_default: bool) -> ModelProfileView:
        from agentcore.assembly.recipes import capability_recipe_for_id

        recipe = capability_recipe_for_id(profile_id)
        assert recipe is not None
        model_id = _capability_main_model_id()
        main = (
            ProfileSlot(origin="platform", model=model_id, provider_id=None)
            if model_id
            else None
        )
        return ModelProfileView(
            id=profile_id,
            name=recipe.name,
            kind="system",
            is_default=is_default,
            recipe=recipe.key,
            omit_factory_catalog=recipe.omit_factory_catalog,
            main=main,
        )

    def _view_row(self, row: LlmModelProfile, *, is_default: bool) -> ModelProfileView:
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
            enabled_mcp_server_ids=_mcp_server_ids(
                getattr(row, "enabled_mcp_server_ids", None)
            ),
            omit_factory_catalog=bool(getattr(row, "omit_factory_catalog", False)),
            main=main,
            worker=_slot_from_row(
                getattr(row, "worker_origin", None),
                getattr(row, "worker_model", None),
                getattr(row, "worker_provider_id", None),
            ),
            background=_slot_from_row(
                getattr(row, "background_origin", None),
                getattr(row, "background_model", None),
                getattr(row, "background_provider_id", None),
            ),
            vision=_slot_from_row(
                getattr(row, "vision_origin", None),
                getattr(row, "vision_model", None),
                getattr(row, "vision_provider_id", None),
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
        return [
            replace(v, is_default=(v.id == effective))
            for v in views
        ]

    async def _capture_legacy_model_star(self, user_id: str) -> None:
        """A leftover per-model preset id is not an assembly. Move the star to 完整."""
        default_id = await self._default_id(user_id)
        if not default_id or is_system_profile_id(default_id):
            return
        if default_id not in system_presets():
            return
        from agentcore.llm.catalog import visible_platform_listable_model_ids

        model_id = system_presets()[default_id]
        await self._users.set_default_model_profile(user_id, system_profile_default_id())
        if model_id not in set(visible_platform_listable_model_ids()):
            return
        real_id = await self._materialize_preset(
            user_id, system_profile_default_id(), set_as_default=True
        )
        await self.update_profile(
            user_id,
            real_id,
            main=ProfileSlot(origin="platform", model=model_id, provider_id=None),
            fields_set={"main"},
        )

    async def list_profiles(self, user_id: str) -> list[ModelProfileView]:
        await self._capture_legacy_model_star(user_id)
        default_id = await self._default_id(user_id)
        hidden = await self._hidden_recipe_keys(user_id)
        views = [
            self._view_system(pid, is_default=False)
            for pid in self._visible_system_ids()
            if not _recipe_key_hidden(pid, hidden)
        ]
        for row in await self._repo.list_for_user(user_id, include_implicit=False):
            views.append(self._view_row(row, is_default=False))
        return self._mark_default(views, default_id)

    async def snapshot_default_profile_id(self, user_id: str) -> str | None:
        """Real assembly id to pin on a new conversation.

        A virtual system preset is materialized into a per-user row first.
        The stored id is never a preset uuid.
        """
        await self._capture_legacy_model_star(user_id)
        hidden = await self._hidden_recipe_keys(user_id)
        default_id = await self._default_id(user_id)
        if default_id and not is_system_profile_id(default_id):
            row = await self._repo.get(default_id, user_id=user_id)
            if row is not None:
                return row.id
        if (
            default_id
            and is_system_profile_id(default_id)
            and not _recipe_key_hidden(default_id, hidden)
        ):
            return await self._materialize_preset(
                user_id, default_id, set_as_default=True
            )
        visible = self._preferred_visible_preset_ids(hidden)
        if visible:
            return await self._materialize_preset(
                user_id, visible[0], set_as_default=True
            )
        rows = await self._repo.list_for_user(user_id, include_implicit=False)
        if rows:
            await self._users.set_default_model_profile(user_id, rows[0].id)
            return rows[0].id
        # Tray can be empty. A new conversation still gets an owned 完整.
        # The hidden chip does not come back.
        return await self._materialize_preset(
            user_id, system_profile_default_id(), set_as_default=True
        )

    def _preferred_visible_preset_ids(self, hidden: frozenset[str]) -> list[str]:
        from agentcore.assembly.recipes import CHAT, FULL, WEB, capability_preset_id

        picked: list[str] = []
        for key in (FULL, WEB, CHAT):
            preset_id = capability_preset_id(key)
            if _recipe_key_hidden(preset_id, hidden):
                continue
            if _system_preset_available(preset_id):
                picked.append(preset_id)
        return picked

    async def _materialize_preset(
        self,
        user_id: str,
        preset_id: str,
        *,
        set_as_default: bool,
        avoid_id: str | None = None,
    ) -> str:
        """Turn a virtual system preset into one owned assembly. Reuses a matching row."""
        from agentcore.assembly.recipes import capability_recipe_for_id

        if not is_system_profile_id(preset_id):
            raise ValidationError("所选装配不存在")
        recipe = capability_recipe_for_id(preset_id)
        assert recipe is not None
        return await self._materialize_capability(
            user_id, recipe, set_as_default=set_as_default, avoid_id=avoid_id
        )

    async def _materialize_capability(
        self,
        user_id: str,
        recipe: object,
        *,
        set_as_default: bool,
        avoid_id: str | None = None,
    ) -> str:
        """Own a row for one official recipe. The recipe key stays until the user edits it."""
        from sqlalchemy import delete

        from agentcore.assembly.recipes import FULL, write_recipe
        from agentcore.db.models import AssemblySkill

        key = recipe.key  # type: ignore[attr-defined]
        for row in await self._repo.list_for_user(user_id, include_implicit=False):
            if row.id == avoid_id:
                continue
            if getattr(row, "recipe", None) == key:
                if set_as_default:
                    await self._users.set_default_model_profile(user_id, row.id)
                return row.id
        had_real_star = False
        star_id = await self._default_id(user_id)
        if star_id and not is_system_profile_id(star_id):
            had_real_star = await self._repo.get(star_id, user_id=user_id) is not None
        view = await self.create_profile(
            user_id,
            name=recipe.name,  # type: ignore[attr-defined]
            set_as_default=set_as_default,
        )
        created = await self._repo.get(view.id, user_id=user_id)
        assert created is not None
        write_recipe(created, recipe)  # type: ignore[arg-type]
        await self._session.commit()
        await self._session.execute(
            delete(AssemblySkill).where(AssemblySkill.assembly_id == created.id)
        )
        await self._session.commit()
        if key == FULL and not had_real_star:
            from agentcore.assembly.membership import seed_assembly_skills_from_account

            await seed_assembly_skills_from_account(self._session, user_id, view.id)
        return view.id

    async def _copy_working_set(
        self, user_id: str, source_id: str, dest: LlmModelProfile
    ) -> None:
        """Copy tools, envelope, plugs, and 交代 from one assembly onto another."""
        src = await self._repo.get(source_id, user_id=user_id)
        if src is None:
            return
        dest.disabled_tools = list(src.disabled_tools or [])
        dest.omitted_projections = list(src.omitted_projections or [])
        dest.enabled_mcp_server_ids = list(src.enabled_mcp_server_ids or [])
        dest.omit_factory_catalog = bool(getattr(src, "omit_factory_catalog", False))
        dest.recipe = getattr(src, "recipe", None)
        dest.omit_desk_rules = bool(getattr(src, "omit_desk_rules", False))
        if (getattr(src, "main_model", None) or "").strip():
            dest.main_origin = src.main_origin
            dest.main_provider_id = src.main_provider_id
            dest.main_model = src.main_model
            dest.worker_origin = src.worker_origin
            dest.worker_provider_id = src.worker_provider_id
            dest.worker_model = src.worker_model
            dest.background_origin = src.background_origin
            dest.background_provider_id = src.background_provider_id
            dest.background_model = src.background_model
            dest.vision_origin = src.vision_origin
            dest.vision_provider_id = src.vision_provider_id
            dest.vision_model = src.vision_model
            dest.reasoning_effort = src.reasoning_effort
            dest.context_budget = getattr(src, "context_budget", None)
        await self._session.commit()
        from agentcore.assembly.membership import copy_assembly_skills

        await copy_assembly_skills(self._session, source_id, dest.id)
        await self._session.refresh(dest)

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
        kind: str = "user",
        set_as_default: bool = False,
    ) -> ModelProfileView:
        name_s = (name or "").strip()
        if not name_s:
            raise ValidationError("装配名称不能为空")
        star_id = await self._default_id(user_id)
        model_id = _capability_main_model_id() or PLATFORM_MODEL_FLASH
        row = await self._repo.create(
            user_id=user_id,
            name=name_s,
            kind=kind,
            main_origin="platform",
            main_model=model_id,
        )
        if star_id and not is_system_profile_id(star_id) and star_id != row.id:
            await self._copy_working_set(user_id, star_id, row)
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
        omit_factory_catalog: bool | None = None,
        main: ProfileSlot | None = None,
        worker: ProfileSlot | None | object = _UNSET,
        background: ProfileSlot | None | object = _UNSET,
        vision: ProfileSlot | None | object = _UNSET,
        reasoning_effort: str | None | object = _UNSET,
        context_budget: int | None | object = _UNSET,
        fields_set: set[str],
    ) -> ModelProfileView:
        if is_system_profile_id(profile_id):
            raise ValidationError("系统预置组合不可编辑")
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise NotFoundError("装配不存在")
        if row.kind == "implicit":
            raise ValidationError("隐式组合不可编辑，请新建用户组合")

        kwargs: dict = {}
        if "name" in fields_set and name is not None:
            name_s = name.strip()
            if not name_s:
                raise ValidationError("装配名称不能为空")
            kwargs["name"] = name_s
        if "enabled_mcp_server_ids" in fields_set:
            kwargs["enabled_mcp_server_ids"] = list(
                _mcp_server_ids(enabled_mcp_server_ids)
            )
        if "omit_factory_catalog" in fields_set and omit_factory_catalog is not None:
            kwargs["omit_factory_catalog"] = bool(omit_factory_catalog)
        if fields_set & {"enabled_mcp_server_ids", "omit_factory_catalog"}:
            kwargs["recipe"] = None
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
        if not (fields_set & {"main", "worker", "background", "vision"}):
            return view
        warn_slots: list[tuple[str, ProfileSlot]] = []
        if view.main is not None:
            warn_slots.append(("main", view.main))
        if view.worker is not None:
            warn_slots.append(("worker", view.worker))
        if view.background is not None:
            warn_slots.append(("background", view.background))
        if view.vision is not None:
            warn_slots.append(("vision", view.vision))
        warnings = await self._byok_reachability_warnings(user_id, warn_slots)
        if not warnings:
            return view
        return replace(view, warnings=warnings)

    async def delete_profile(self, user_id: str, profile_id: str) -> None:
        """Drop an assembly. A starred one moves the star first.

        An official recipe leaves the tray and stays in code. An owned row,
        including the star, is deleted. Conversations pinned here move to
        whatever is starred afterward.
        """
        if is_system_profile_id(profile_id):
            await self._dismiss_preset(user_id, profile_id)
            return
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise NotFoundError("模型组合不存在")
        await self._repoint_pins(user_id, profile_id)
        deleted = await self._repo.delete(profile_id, user_id=user_id)
        if not deleted:
            raise NotFoundError("模型组合不存在")

    async def _dismiss_preset(self, user_id: str, profile_id: str) -> None:
        from agentcore.assembly.recipes import capability_recipe_for_id

        recipe = capability_recipe_for_id(profile_id)
        if recipe is None:
            raise NotFoundError("装配不存在")
        await self._users.hide_capability_recipe(user_id, recipe.key)
        await self._repoint_pins(user_id, profile_id)

    async def _replacement_star(self, user_id: str, *, excluding: str) -> str | None:
        """Next star after ``excluding`` leaves: another owned row, else a tray recipe."""
        for row in await self._repo.list_for_user(user_id, include_implicit=False):
            if row.id != excluding and row.kind == "user":
                return row.id
        hidden = await self._hidden_recipe_keys(user_id)
        for preset_id in self._preferred_visible_preset_ids(hidden):
            if preset_id == excluding:
                continue
            # Chats store an owned id. A tray recipe is materialized first.
            # Do not reuse the row that is about to be deleted.
            return await self._materialize_preset(
                user_id,
                preset_id,
                set_as_default=False,
                avoid_id=excluding,
            )
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
            real_id = await self._materialize_preset(
                user_id, profile_id, set_as_default=True
            )
            row = await self._repo.get(real_id, user_id=user_id)
            if row is None:
                raise NotFoundError("装配不存在")
            return self._view_row(row, is_default=True)
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise NotFoundError("模型组合不存在")
        if row.kind == "implicit":
            raise ValidationError("不能将隐式组合设为账号默认")
        await self._users.set_default_model_profile(user_id, profile_id)
        return self._view_row(row, is_default=True)

    async def ensure_profile_usable(self, user_id: str, profile_id: str) -> str:
        """Return an owned assembly id. A system preset is materialized first."""
        if is_system_profile_id(profile_id):
            return await self._materialize_preset(
                user_id, profile_id, set_as_default=False
            )
        row = await self._repo.get(profile_id, user_id=user_id)
        if row is None:
            raise ValidationError("所选装配不存在或不属于你")
        return profile_id

    async def _expand_row(self, user_id: str, row: LlmModelProfile) -> ExpandedProfile:
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
        kind: ProfileKind = "implicit" if row.kind == "implicit" else "user"
        return ExpandedProfile(
            profile_id=row.id,
            name=row.name,
            kind=kind,
            main=main,
            worker=worker,
            background=background,
            vision=vision,
            reasoning_effort=_snap_reasoning_effort(
                main.model, getattr(row, "reasoning_effort", None)
            ),
            context_budget=snap_context_budget(
                main.model, getattr(row, "context_budget", None)
            ),
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

    async def expand_for_conversation(
        self, user_id: str, conv
    ) -> ExpandedProfile:
        assembly_id = getattr(conv, "assembly_id", None) or None
        return await self.expand(user_id, assembly_id)
