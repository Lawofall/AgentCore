"""Platform LLM upstream, vendor keys, and billing settings."""

from __future__ import annotations

import json
from typing import Self

from pydantic import BaseModel, Field, model_validator


class PlatformSettings(BaseModel):
    platform_api_key: str = ""
    # Optional operator alias for the shared default key (logs + cost_calls only).
    # Empty = stable hash of (api_key, base_url). Never a key / last-4.
    platform_credential_id: str = ""
    platform_base_url: str = "https://api.deepseek.com"
    platform_model: str = "deepseek-v4.1-flash"
    # Title chrome. Product pin = Flash; empty env falls back to platform_model.
    # Compaction follows the conversation's chat model, not this field.
    platform_background_model: str = "deepseek-v4.1-flash"
    # Explicit platform model catalog allowlist (运营配置, 成本配额与计费 §〇·六 F3):
    # comma-separated ids the operator subsidizes. Empty = fall
    # back to platform_model (+ background). Every listed id MUST have a curated price
    # card (F4) or it is hard-excluded from catalog / system presets; discovery of the
    # full upstream set is deliberately NOT used here (stays a BYOK-row concern).
    # When non-empty, PLATFORM_MODEL and (if set) PLATFORM_BACKGROUND_MODEL must be
    # members — fail-fast at settings load (no silent drift).
    platform_models: str = ""
    # Per-model platform credential overrides (「一 key 一模型」, 成本配额与计费
    # §〇·六 F3): a JSON object mapping model id →
    # {"api_key"?, "base_url"?, "upstream_model"?, "id"?}.
    # When a model in the catalog has an entry **with its own api_key**, that
    # pair wins (missing base_url → PLATFORM_BASE_URL). Otherwise the operator
    # pool's first enabled member is used as a bound pair; a base_url-only
    # override is not mixed with a pool key (it still applies on the empty-pool
    # env fallback).
    # Optional upstream_model: catalog id may differ from the id sent to the upstream
    # (e.g. glm-5.2-alt → glm-5.2). Optional id: operator alias for
    # logs / cost_calls.platform_credential_id. Empty = every platform model shares
    # the default key/base_url; omitted upstream_model = send catalog id as-is.
    platform_model_credentials: str = ""

    # --- 多厂商 provider（OpenAI 兼容，经 ProviderRouter 按 provider/model 前缀路由） ---
    moonshot_api_key: str = ""
    moonshot_base_url: str = "https://api.moonshot.cn/v1"
    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    doubao_api_key: str = ""
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"

    # --- 计费模式 ---
    billing_mode: str = "byok"

    # OpenCode Go monthly window anniversary (UTC day-of-month, 1–31). Short
    # months clamp to the last day. Used as the empty-pool / env-fallback
    # aggregate on admin Go-window calibration. Pool members each carry their
    # own subscription_day (accounts are bought in batches). Ops must set the
    # env value to the real billing anniversary of the env key — default 1
    # is only a bootable fallback.
    platform_go_subscription_day: int = Field(default=1, ge=1, le=31)

    # AES-256-GCM 主密钥：BYOK API Key、平台额度账号池、Git PAT、admin TOTP。
    encryption_key: str = ""

    @model_validator(mode="after")
    def _platform_models_allowlist_membership(self) -> Self:
        """Fail-fast when PLATFORM_MODELS is set but defaults sit outside it."""
        raw = self.platform_models or ""
        seen: set[str] = set()
        allowlist: list[str] = []
        for part in raw.split(","):
            mid = part.strip()
            if mid and mid not in seen:
                seen.add(mid)
                allowlist.append(mid)
        if not allowlist:
            return self
        platform_model = (self.platform_model or "").strip()
        if platform_model and platform_model not in seen:
            raise ValueError(
                f"PLATFORM_MODEL={platform_model!r} must be in PLATFORM_MODELS "
                f"({', '.join(allowlist)}); empty PLATFORM_MODELS skips this check"
            )
        background = (self.platform_background_model or "").strip()
        if background and background not in seen:
            raise ValueError(
                f"PLATFORM_BACKGROUND_MODEL={background!r} must be in PLATFORM_MODELS "
                f"({', '.join(allowlist)}); empty PLATFORM_MODELS skips this check"
            )
        return self


def parse_platform_model_credentials(raw: str) -> dict[str, dict[str, str]]:
    """Parse ``PLATFORM_MODEL_CREDENTIALS`` JSON into model credential maps.

    Shape: ``{model_id: {api_key?, base_url?, upstream_model?, id?}}``.

    Malformed JSON / wrong shape degrades to ``{}`` (logged) so an operator typo never
    crashes a turn — the platform then serves every model on the shared default key.
    Only non-blank ``api_key`` / ``base_url`` / ``upstream_model`` / ``id`` fields are
    kept; an empty object drops out. ``id`` is an operator alias for the resolved
    ``(api_key, base_url)`` pair (logs + ``cost_calls`` only).
    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        from agentcore.core.logging import get_logger

        get_logger(__name__).warning("platform.model_credentials_parse_failed", error=str(e))
        return {}
    if not isinstance(data, dict):
        from agentcore.core.logging import get_logger

        get_logger(__name__).warning("platform.model_credentials_not_object")
        return {}
    result: dict[str, dict[str, str]] = {}
    for model_id, entry in data.items():
        mid = str(model_id).strip()
        if not mid or not isinstance(entry, dict):
            continue
        creds: dict[str, str] = {}
        api_key = str(entry.get("api_key", "") or "").strip()
        base_url = str(entry.get("base_url", "") or "").strip()
        upstream_model = str(entry.get("upstream_model", "") or "").strip()
        if api_key:
            creds["api_key"] = api_key
        if base_url:
            creds["base_url"] = base_url
        if upstream_model:
            creds["upstream_model"] = upstream_model
        ident = str(entry.get("id", "") or "").strip()
        if ident:
            creds["id"] = ident
        if creds:
            result[mid] = creds
    return result
