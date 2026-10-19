"""BYOK LLM provider configuration service (the write + admin surface).

The read path (resolve a turn's credentials, never raises) lives in ``llm/resolve.py``.
This module is its WRITE counterpart for 设置·模型配置 over a LIST of providers: add /
edit / remove / connectivity-test each OpenAI-compatible endpoint. Account model
selection uses ``llm/model_profiles.py`` (模型组合) — not per-slot pointers here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.billing.allowance import invalidate_allowance
from agentcore.billing.preference import (
    platform_catalog_visible,
)
from agentcore.config import settings
from agentcore.core.errors import (
    BYOKKeyMissingError,
    KeyStorageUnavailableError,
    NotFoundError,
    ValidationError,
)
from agentcore.core.logging import get_logger
from agentcore.db.base import async_session_factory
from agentcore.db.models import UserLlmProvider
from agentcore.db.repositories import (
    LlmModelProfileRepository,
    UserLlmProviderRepository,
    UserRepository,
)
from agentcore.llm.byok_provider_presets import (
    canonicalize_byok_api_root,
    parse_vendor_id,
    preset_for_vendor,
    seed_model_for_vendor,
)
from agentcore.llm.factory import build_provider
from agentcore.llm.model_profiles import ProfileSlot
from agentcore.llm.model_reachability import (
    CONNECTIVITY_POLICY,
    ModelListOutcome,
    check_model_reachable,
    fetch_model_list,
)
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.resolve import _decrypt_provider, resolve_provider_credentials
from agentcore.security.keys import KeyEncryptor
from agentcore.security.mask import mask_secret

logger = get_logger(__name__)

# Shown when connectivity test succeeds with no other message — green ≠ chat-ready.
CONNECTIVITY_OK_HINT = (
    "连接正常。已验证服务商连通（GET /models 或能力上的模型）。"
    "日常聊天请在输入框选模型。"
)
CONNECTIVITY_NO_CATALOG = (
    "上游未列出模型（无 GET /models 或列表为空）。"
    "可在输入框手填模型 ID；测连不代填模型。"
)
CONNECTIVITY_NOT_API_ROOT = (
    "这个地址返回的不是模型目录（像是网页）。"
    "请确认填的是 API 地址；控制台只给域名时会补 /v1。"
)


def _connectivity_no_models_copy(model_list: ModelListOutcome) -> str:
    """Empty / missing catalog vs HTML-or-invalid JSON at ``GET /models``."""
    message = model_list.message or ""
    if "格式无效" in message or "缺少 data" in message:
        return CONNECTIVITY_NOT_API_ROOT
    return CONNECTIVITY_NO_CATALOG


@dataclass(frozen=True)
class LlmProviderView:
    """Settings view of one BYOK provider — never the plaintext key."""

    id: str
    label: str
    base_url: str
    vendor_id: str
    status: str
    masked_key: str | None = None
    supports_tools: bool | None = None
    message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class LlmProvidersView:
    """Provider list + deployment caps (+ account default profile id)."""

    providers: list[LlmProviderView] = field(default_factory=list)
    default_assembly_id: str | None = None
    billing_mode: str = "byok"
    platform_available: bool = False
    platform_model: str | None = None


_OFFICIAL_DEEPSEEK_HOST = "https://api.deepseek.com"
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True)
class OfficialDeepSeekProvider:
    """Official-host BYOK row that can call DeepSeek search."""

    provider_id: str
    api_key: str
    label: str


def _is_official_deepseek_host(url: str) -> bool:
    text = (url or "").strip().rstrip("/").lower()
    if text.endswith("/v1"):
        text = text[: -len("/v1")].rstrip("/")
    return text == _OFFICIAL_DEEPSEEK_HOST


async def official_deepseek_provider_label(user_id: str) -> str | None:
    """Settings label when the account has an official DeepSeek service."""
    row = await _first_official_deepseek_row(user_id)
    if row is None:
        return None
    return (getattr(row, "label", None) or "").strip() or "DeepSeek"


async def load_official_deepseek_provider(user_id: str) -> OfficialDeepSeekProvider | None:
    """Decrypt the first official DeepSeek BYOK key. None if there isn't one."""
    row = await _first_official_deepseek_row(user_id)
    if row is None:
        return None
    creds = _decrypt_provider(row, user_id)
    api_key = (creds.api_key if creds is not None else "").strip()
    if not api_key:
        return None
    return OfficialDeepSeekProvider(
        provider_id=row.id,
        api_key=api_key,
        label=(row.label or "").strip() or "DeepSeek",
    )


async def _first_official_deepseek_row(user_id: str):
    if not _UUID_RE.fullmatch(user_id or ""):
        return None
    async with async_session_factory() as session:
        rows = await UserLlmProviderRepository(session).list_for_user(user_id)
    for row in rows:
        if _is_official_deepseek_host(getattr(row, "base_url", "") or ""):
            return row
    return None


def _mask_key_ciphertext(enc: KeyEncryptor | None, api_key_enc: bytes) -> str | None:
    if enc is None or not api_key_enc:
        return None
    try:
        plaintext = enc.decrypt(api_key_enc).decode()
    except Exception:  # noqa: BLE001
        return None
    return mask_secret(plaintext)


class LlmProviderService:
    """Add / edit / remove / connectivity-test a user's BYOK providers."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = UserLlmProviderRepository(session)
        self._users = UserRepository(session)
        self._profiles = LlmModelProfileRepository(session)

    def _encryptor(self) -> KeyEncryptor | None:
        if not settings.encryption_key:
            return None
        try:
            return KeyEncryptor(settings.encryption_key)
        except ValueError:
            logger.error("byok.key_malformed")
            return None

    def _view(
        self,
        row: UserLlmProvider,
        *,
        enc: KeyEncryptor | None,
        message: str | None = None,
    ) -> LlmProviderView:
        return LlmProviderView(
            id=row.id,
            label=row.label or "",
            base_url=canonicalize_byok_api_root(row.base_url or ""),
            vendor_id=(row.vendor_id or "").strip() or "custom",
            status=row.status,
            masked_key=_mask_key_ciphertext(enc, row.api_key_enc),
            supports_tools=row.supports_tools,
            message=message,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def list_providers(self, user_id: str) -> LlmProvidersView:
        enc = self._encryptor()
        user = await self._users.get_by_id(user_id)
        rows = await self._repo.list_for_user(user_id)
        providers = [self._view(row, enc=enc) for row in rows]
        platform_available = platform_catalog_visible()
        return LlmProvidersView(
            providers=providers,
            default_assembly_id=(
                getattr(user, "default_assembly_id", None) if user else None
            ),
            billing_mode=settings.billing_mode,
            platform_available=platform_available,
            platform_model=settings.platform_model if platform_available else None,
        )

    async def create_provider(
        self,
        user_id: str,
        *,
        label: str,
        api_key: str,
        vendor_id: str,
        base_url: str | None = None,
    ) -> LlmProviderView:
        """Add a provider.

        First provider + matching vendor preset writes the starred assembly's main.
        """
        api_key = (api_key or "").strip()
        if not api_key:
            raise ValidationError("API Key 不能为空")
        from agentcore.llm.credentials import require_http_header_safe_api_key

        api_key = require_http_header_safe_api_key(api_key)
        enc = self._encryptor()
        if enc is None:
            raise KeyStorageUnavailableError(
                "服务端未配置加密主密钥，暂时无法保存 API Key，请联系管理员"
            )
        parsed_vendor = parse_vendor_id(vendor_id)
        if parsed_vendor is None:
            raise ValidationError("未知的厂商预设")
        preset = preset_for_vendor(parsed_vendor)
        resolved_base_url = (base_url or "").strip() or (
            preset.base_url if preset is not None else settings.platform_base_url
        )
        if not resolved_base_url:
            raise ValidationError("Base URL 不能为空")
        resolved_base_url = canonicalize_byok_api_root(resolved_base_url)
        seed = seed_model_for_vendor(parsed_vendor)

        was_empty = (await self._repo.count_for_user(user_id)) == 0
        row = await self._repo.create(
            user_id=user_id,
            label=label,
            api_key_enc=enc.encrypt(api_key.encode()),
            base_url=resolved_base_url,
            vendor_id=parsed_vendor,
            default_model=seed,
        )
        if was_empty and seed:
            from agentcore.llm.model_profiles import LlmModelProfileService

            profiles = LlmModelProfileService(self._session)
            assembly_id = await profiles.snapshot_default_profile_id(user_id)
            if assembly_id:
                await profiles.update_profile(
                    user_id,
                    assembly_id,
                    main=ProfileSlot(origin="byok", model=seed, provider_id=row.id),
                    fields_set={"main"},
                )
        # A different upstream is being asked now: anything cached about the old one
        # refusing this account (背景整理的申报冷却) no longer describes reality — and
        # 「接入自己的 key」is the very exit the 429 copy offers.
        invalidate_allowance(user_id, reason="byok_provider_changed")
        return self._view(row, enc=enc)

    async def update_provider(
        self,
        user_id: str,
        provider_id: str,
        *,
        label: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        vendor_id: str | None = None,
        fields_set: set[str],
    ) -> LlmProviderView:
        existing = await self._repo.get(provider_id, user_id=user_id)
        if existing is None:
            raise NotFoundError("服务商不存在")

        kwargs: dict[str, object] = {}
        if "label" in fields_set:
            kwargs["label"] = label or ""
        if "api_key" in fields_set and (api_key or "").strip():
            enc = self._encryptor()
            if enc is None:
                raise KeyStorageUnavailableError(
                    "服务端未配置加密主密钥，暂时无法保存 API Key，请联系管理员"
                )
            from agentcore.llm.credentials import require_http_header_safe_api_key

            safe_key = require_http_header_safe_api_key(api_key.strip())
            kwargs["api_key_enc"] = enc.encrypt(safe_key.encode())
        if "vendor_id" in fields_set:
            parsed_vendor = parse_vendor_id(vendor_id)
            if parsed_vendor is None:
                raise ValidationError("未知的厂商预设")
            kwargs["vendor_id"] = parsed_vendor
            kwargs["default_model"] = seed_model_for_vendor(parsed_vendor)
        if "base_url" in fields_set:
            resolved = (base_url or settings.platform_base_url).strip()
            if not resolved:
                raise ValidationError("Base URL 不能为空")
            kwargs["base_url"] = canonicalize_byok_api_root(resolved)
            next_vendor = kwargs.get("vendor_id") or existing.vendor_id
            kwargs.setdefault("default_model", seed_model_for_vendor(str(next_vendor)))

        row = await self._repo.update(provider_id, user_id=user_id, **kwargs)  # type: ignore[arg-type]
        assert row is not None
        # Only a credential-shaped edit changes what upstream would answer; renaming
        # the 服务商 does not, so it must not retire a cooldown that still holds.
        if kwargs.keys() & {"api_key_enc", "base_url", "vendor_id"}:
            invalidate_allowance(user_id, reason="byok_provider_changed")
        return self._view(row, enc=self._encryptor())

    async def delete_provider(self, user_id: str, provider_id: str) -> None:
        """Remove a provider; profile slots referencing it are cleared / retargeted."""
        removed = await self._repo.delete(provider_id, user_id=user_id)
        if not removed:
            raise NotFoundError("服务商不存在")

        fallback = await self._repo.first_for_user(user_id)
        if fallback is not None:
            seed = seed_model_for_vendor(fallback.vendor_id)
            await self._profiles.retarget_main_provider(
                user_id,
                from_provider_id=provider_id,
                to_provider_id=fallback.id,
                to_model=seed or None,
                to_origin="byok",
            )
        else:
            await self._profiles.retarget_main_provider(
                user_id,
                from_provider_id=provider_id,
                to_provider_id=None,
                to_model=DEEPSEEK_V4_FLASH,
                to_origin="platform",
            )
        await self._profiles.clear_provider_refs(user_id, provider_id)
        invalidate_allowance(user_id, reason="byok_provider_changed")

    async def test_provider(self, user_id: str, provider_id: str) -> LlmProviderView:
        row = await self._repo.get(provider_id, user_id=user_id)
        if row is None:
            raise NotFoundError("服务商不存在")
        if not row.api_key_enc:
            raise BYOKKeyMissingError("尚未配置 API Key，无法测试连接")
        credentials = await resolve_provider_credentials(self._session, user_id, provider_id)
        enc = self._encryptor()
        if credentials is None:
            await self._repo.update_status(provider_id, "error")
            fresh = await self._repo.get(provider_id, user_id=user_id)
            assert fresh is not None
            return self._view(
                fresh,
                enc=enc,
                message="无法解密已保存的 Key（服务端密钥变更或数据损坏），请重新填写",
            )
        # User-facing errors use credentials.label (never internal source ``user``).
        provider = build_provider(credentials)
        profile_models = await self._profile_models_for_provider(user_id, provider_id)
        base_url = credentials.base_url
        supports_tools: bool | None = None
        logger.info(
            "llm_provider.test.start",
            user_id=user_id,
            provider_id=provider_id,
            base_url=base_url,
            profile_models=profile_models,
            provider_type=type(provider).__name__,
        )
        try:
            status, message, supports_tools = await self._run_connectivity_test(
                provider,
                profile_models=profile_models,
            )
            if status == "error":
                logger.warning(
                    "llm_provider.test.failed",
                    user_id=user_id,
                    provider_id=provider_id,
                    base_url=base_url,
                    error=message,
                )
            else:
                logger.info(
                    "llm_provider.test.ok",
                    user_id=user_id,
                    provider_id=provider_id,
                    base_url=base_url,
                    supports_tools=supports_tools,
                )
        except Exception:
            logger.exception(
                "llm_provider.test.unhandled",
                user_id=user_id,
                provider_id=provider_id,
                base_url=base_url,
                provider_type=type(provider).__name__,
            )
            raise
        finally:
            await provider.close()
        await self._repo.update_status(provider_id, status)
        if status == "active":
            await self._repo.update_supports_tools(provider_id, supports_tools)
            if not message:
                message = CONNECTIVITY_OK_HINT
        fresh = await self._repo.get(provider_id, user_id=user_id)
        assert fresh is not None
        return self._view(fresh, enc=enc, message=message)

    async def _profile_models_for_provider(
        self, user_id: str, provider_id: str
    ) -> list[str]:
        """Distinct model ids from profile slots that point at this BYOK provider."""
        rows = await self._profiles.list_for_user(user_id, include_implicit=True)
        found: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for origin, pid, model in (
                (row.main_origin, row.main_provider_id, row.main_model),
                (row.worker_origin, row.worker_provider_id, row.worker_model),
            ):
                if origin != "byok" or pid != provider_id:
                    continue
                model_s = (model or "").strip()
                if not model_s or model_s in seen:
                    continue
                seen.add(model_s)
                found.append(model_s)
        return found

    async def _run_connectivity_test(
        self,
        provider: object,
        *,
        profile_models: list[str] | None = None,
    ) -> tuple[str, str | None, bool | None]:
        """Prove the endpoint via ``GET /models``; probe only 模型组合 ids.

        Auth / balance failures from ``list_models`` are hard errors. A non-empty
        upstream list is enough to mark the provider connected — we do not guess
        a chat model just to green the test. Empty / missing ``/models`` is not
        a fake green: if the account already pointed 模型组合 slots at this
        provider, those ids are probed (Ark ``ep-`` etc.); otherwise the caller
        is told to fill a model id in 模型组合. Tools probing is best-effort and
        never flips an otherwise-active result to error.
        """
        model_list = await fetch_model_list(provider)
        if model_list.kind == "hard_error":
            return "error", model_list.message, None

        listed = model_list.kind == "ok" and bool(model_list.model_ids)
        extras = [
            extra.strip() for extra in (profile_models or ()) if extra and extra.strip()
        ]
        if not listed and not extras:
            return "error", _connectivity_no_models_copy(model_list), None

        for extra_s in extras:
            extra_reach, extra_msg = await check_model_reachable(
                provider,
                model=extra_s,
                model_list=model_list,
                policy=CONNECTIVITY_POLICY,
            )
            if extra_reach == "error":
                detail = extra_msg or "连接失败"
                return (
                    "error",
                    f"能力引用的模型「{extra_s}」不可用：{detail}",
                    None,
                )

        tools_model = ""
        if model_list.kind == "ok" and model_list.model_ids:
            tools_model = model_list.model_ids[0]
        elif extras:
            tools_model = extras[0]
        supports_tools = await self._best_effort_probe_tools(provider, model=tools_model)
        return "active", None, supports_tools

    @staticmethod
    async def _best_effort_probe_tools(provider: object, *, model: str) -> bool | None:
        probe_tools = getattr(provider, "probe_tools", None)
        if not callable(probe_tools) or not model:
            return None
        try:
            return await probe_tools(model=model)
        except Exception:  # noqa: BLE001 — tools probe must not fail the whole test
            logger.warning(
                "llm_provider.test.probe_tools_failed",
                model=model,
                exc_info=True,
            )
            return None
