"""Unit tests for LlmProviderService (BYOK 多服务商 write path · 模型组合硬切后)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

from agentcore.config import settings
from agentcore.core.errors import (
    KeyStorageUnavailableError,
    LLMError,
    LLMInsufficientBalanceError,
    NotFoundError,
    ValidationError,
)
from agentcore.llm.credentials import LLMCredentials
from agentcore.llm.model_reachability import (
    ModelListOutcome,
    check_model_reachable,
)
from agentcore.llm.profiles import DEEPSEEK_V4_FLASH
from agentcore.llm.provider_service import LlmProviderService

pytestmark = pytest.mark.anyio


def _row(**kwargs):
    defaults = {
        "id": "prov-1",
        "user_id": "u1",
        "label": "DeepSeek",
        "api_key_enc": b"cipher",
        "base_url": settings.platform_base_url,
        "default_model": DEEPSEEK_V4_FLASH,
        "supports_tools": None,
        "status": "unchecked",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _user(**kwargs):
    defaults = {
        "user_id": "u1",
        "default_assembly_id": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.fixture
def service():
    svc = LlmProviderService(MagicMock())
    svc._repo = MagicMock()
    svc._users = MagicMock()
    svc._profiles = MagicMock()
    svc._repo.count_for_user = AsyncMock(return_value=0)
    svc._repo.create = AsyncMock(return_value=_row())
    svc._repo.get = AsyncMock(return_value=_row())
    svc._repo.list_for_user = AsyncMock(return_value=[_row()])
    svc._repo.update = AsyncMock(return_value=_row())
    svc._repo.update_status = AsyncMock()
    svc._repo.update_supports_tools = AsyncMock()
    svc._repo.first_for_user = AsyncMock(return_value=None)
    svc._repo.delete = AsyncMock(return_value=True)
    svc._users.get_by_id = AsyncMock(return_value=_user())
    svc._profiles.retarget_main_provider = AsyncMock()
    svc._profiles.clear_provider_refs = AsyncMock()
    svc._profiles.list_for_user = AsyncMock(return_value=[])
    return svc


def _enc():
    enc = MagicMock()
    enc.encrypt.return_value = b"cipher"
    enc.decrypt.return_value = b"sk-secret-1234"
    return enc


async def test_create_provider_first_seeds_current_config_profile(service):
    from agentcore.llm.byok_provider_presets import seed_model_for_base_url

    with (
        patch.object(service, "_encryptor", return_value=_enc()),
        patch(
            "agentcore.llm.model_profiles.LlmModelProfileService.snapshot_default_profile_id",
            new=AsyncMock(return_value="asm-1"),
        ) as snapshot,
        patch(
            "agentcore.llm.model_profiles.LlmModelProfileService.update_profile",
            new=AsyncMock(),
        ) as update_profile,
    ):
        view = await service.create_provider(
            "u1", label="DeepSeek", api_key="sk-secret-1234"
        )
    snapshot.assert_awaited_once()
    update_profile.assert_awaited_once()
    kwargs = update_profile.await_args.kwargs
    assert kwargs["fields_set"] == {"main"}
    assert kwargs["main"].origin == "byok"
    assert kwargs["main"].provider_id == "prov-1"
    assert kwargs["main"].model == seed_model_for_base_url(settings.platform_base_url)
    assert view.id == "prov-1"
    assert view.masked_key == "••••1234"
    assert not hasattr(view, "default_model")


async def test_create_provider_custom_url_does_not_seed_profile(service):
    service._repo.create = AsyncMock(
        return_value=_row(base_url="https://my-proxy.example/v1", default_model="")
    )
    with (
        patch.object(service, "_encryptor", return_value=_enc()),
        patch(
            "agentcore.llm.model_profiles.LlmModelProfileService.snapshot_default_profile_id",
            new=AsyncMock(),
        ) as create_profile,
    ):
        await service.create_provider(
            "u1",
            label="Gateway",
            api_key="sk-secret-1234",
            base_url="https://my-proxy.example/v1",
        )
    create_profile.assert_not_awaited()


async def test_create_provider_second_does_not_seed_profile(service):
    service._repo.count_for_user = AsyncMock(return_value=1)
    with (
        patch.object(service, "_encryptor", return_value=_enc()),
        patch(
            "agentcore.llm.model_profiles.LlmModelProfileService.snapshot_default_profile_id",
            new=AsyncMock(),
        ) as create_profile,
    ):
        await service.create_provider("u1", label="Kimi", api_key="sk-second-key")
    create_profile.assert_not_awaited()


async def test_create_provider_rejects_empty_key(service):
    with pytest.raises(ValidationError):
        await service.create_provider("u1", label="X", api_key="   ")


async def test_create_provider_rejects_fullwidth_in_key(service):
    """Fullwidth punctuation in Key → ValidationError (not UnicodeEncodeError 500)."""
    with (
        patch.object(service, "_encryptor", return_value=_enc()),
        pytest.raises(ValidationError, match="全角|中文符号"),
    ):
        await service.create_provider(
            "u1", label="X", api_key="sk-test（revoked）"
        )


async def test_update_provider_rejects_fullwidth_in_key(service):
    with (
        patch.object(service, "_encryptor", return_value=_enc()),
        pytest.raises(ValidationError, match="全角|中文符号"),
    ):
        await service.update_provider(
            "u1",
            "prov-1",
            api_key="sk-bad（x）",
            fields_set={"api_key"},
        )


async def test_create_provider_without_master_key_raises(service):
    with (
        patch.object(service, "_encryptor", return_value=None),
        pytest.raises(KeyStorageUnavailableError),
    ):
        await service.create_provider("u1", label="X", api_key="sk-x")


async def test_list_providers_reports_profile_id_and_platform(service, monkeypatch):
    from agentcore.llm.model_profiles import platform_preset_id

    preset = platform_preset_id("glm-5.2")
    monkeypatch.setattr(settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(settings, "billing_mode", "platform")
    service._repo.list_for_user = AsyncMock(return_value=[])
    service._users.get_by_id = AsyncMock(
        return_value=_user(default_assembly_id=preset)
    )
    view = await service.list_providers("u1")
    assert view.providers == []
    assert view.default_assembly_id == preset
    assert view.platform_available is True


async def test_list_providers_platform_signal_false_when_dormant(service, monkeypatch):
    """byok + key still present → platform_available false."""
    monkeypatch.setattr(settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(settings, "billing_mode", "byok")
    monkeypatch.setattr(settings, "platform_model", "plat-model")
    service._repo.list_for_user = AsyncMock(return_value=[])
    service._users.get_by_id = AsyncMock(return_value=_user())
    view = await service.list_providers("u1")
    assert view.platform_available is False
    assert view.platform_model is None


class _FakeProbeProvider:
    def __init__(
        self,
        *,
        fail: bool = False,
        supports_tools: bool | None = None,
        model_ids: list[str] | None = None,
        list_models_error: Exception | None = None,
        probe_error: Exception | None = None,
        probe_tools_raises: bool = False,
        fail_models: set[str] | None = None,
    ) -> None:
        self._fail = fail
        self._supports_tools = supports_tools
        self._model_ids = model_ids
        self._list_models_error = list_models_error
        self._probe_error = probe_error
        self._probe_tools_raises = probe_tools_raises
        self._fail_models = fail_models or set()
        self.probe_model: str | None = None
        self.probe_models: list[str] = []
        self.list_models_called = False
        self.probe_called = False

    async def list_models(self) -> list[str]:
        self.list_models_called = True
        if self._list_models_error is not None:
            raise self._list_models_error
        if self._model_ids is None:
            raise LLMError("list_models unavailable")
        return list(self._model_ids)

    async def probe(self, *, model: str) -> None:
        self.probe_called = True
        self.probe_model = model
        self.probe_models.append(model)
        if self._probe_error is not None:
            raise self._probe_error
        if self._fail or model in self._fail_models:
            raise LLMError("bad key" if self._fail else f"model {model} not found")

    async def probe_tools(self, *, model: str) -> bool | None:
        if self._probe_tools_raises:
            raise RuntimeError("tools probe boom")
        return self._supports_tools

    async def close(self) -> None:
        pass


async def test_test_provider_records_active_and_tools(service):
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x"),
            _row(api_key_enc=b"x", status="active", supports_tools=True),
        ]
    )
    creds = LLMCredentials(
        api_key="sk-abc", base_url="https://api.openai.com/v1", default_model="gpt-4o"
    )
    fake = _FakeProbeProvider(model_ids=["gpt-4o", "gpt-4o-mini"], supports_tools=True)
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake) as build,
        patch.object(service, "_encryptor", return_value=_enc()),
        capture_logs() as caps,
    ):
        view = await service.test_provider("u1", "prov-1")
    build.assert_called_once()
    assert "display_name" not in build.call_args.kwargs
    assert fake.list_models_called is True
    assert fake.probe_called is False
    assert view.status == "active"
    assert view.message is not None
    assert "连通" in view.message and "装配" in view.message
    service._repo.update_status.assert_awaited_once_with("prov-1", "active")
    service._repo.update_supports_tools.assert_awaited_once_with("prov-1", True)
    start = next(c for c in caps if c.get("event") == "llm_provider.test.start")
    assert start["provider_id"] == "prov-1"
    assert start["profile_models"] == []
    assert start["base_url"] == "https://api.openai.com/v1"
    ok = next(c for c in caps if c.get("event") == "llm_provider.test.ok")
    assert ok["supports_tools"] is True


async def test_test_provider_empty_models_list_without_profile_is_not_green(service):
    """JSON data:[] without 模型组合 ids must not soft-green."""
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x"),
            _row(api_key_enc=b"x", status="error"),
        ]
    )
    creds = LLMCredentials(
        api_key="sk-abc", base_url="https://gw.example/v1", default_model=""
    )
    fake = _FakeProbeProvider(model_ids=[], supports_tools=None)
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert fake.list_models_called is True
    assert fake.probe_called is False
    assert view.status == "error"
    assert "未列出模型" in (view.message or "")
    assert "装配" in (view.message or "")


async def test_test_provider_nonempty_list_does_not_probe_unlisted_seed(service):
    """A leftover stored seed absent from /models must not be probed."""
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x", label="My Gateway"),
            _row(api_key_enc=b"x", status="active", supports_tools=None),
        ]
    )
    creds = LLMCredentials(
        api_key="sk-abc", base_url="https://gw.example/v1", default_model="stale-model"
    )
    fake = _FakeProbeProvider(model_ids=["gpt-4o"], supports_tools=None)
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake) as build,
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert "display_name" not in build.call_args.kwargs
    assert fake.list_models_called is True
    assert fake.probe_called is False
    assert view.status == "active"
    assert view.message is not None
    assert "连通" in view.message and "装配" in view.message
    service._repo.update_status.assert_awaited_once_with("prov-1", "active")


async def test_test_provider_profile_model_missing_from_list_probe_failure_is_error(
    service,
):
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x"),
            _row(api_key_enc=b"x", status="error"),
        ]
    )
    service._profiles.list_for_user = AsyncMock(
        return_value=[_profile_row_ref(background_model="stale-model")]
    )
    creds = LLMCredentials(
        api_key="sk-abc", base_url="https://gw.example/v1", default_model=""
    )
    fake = _FakeProbeProvider(
        model_ids=["gpt-4o"], fail_models={"stale-model"}, supports_tools=None
    )
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert fake.list_models_called is True
    assert fake.probe_called is True
    assert "stale-model" in fake.probe_models
    assert view.status == "error"
    assert "stale-model" in (view.message or "")
    assert "装配" in (view.message or "")


async def test_test_provider_profile_ark_ep_missing_from_list_still_active_via_combo(
    service,
):
    """Ark-style ep- id in 模型组合 may be absent from /models yet still chat."""
    ep = "ep-20240101000000-abcde"
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x"),
            _row(api_key_enc=b"x", status="active", supports_tools=True),
        ]
    )
    service._profiles.list_for_user = AsyncMock(
        return_value=[_profile_row_ref(background_model=ep)]
    )
    creds = LLMCredentials(
        api_key="sk-abc",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        default_model="",
    )
    fake = _FakeProbeProvider(
        model_ids=["doubao-pro-32k", "doubao-lite-32k"],
        fail=False,
        supports_tools=True,
    )
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert fake.list_models_called is True
    assert ep in fake.probe_models
    assert view.status == "active"
    assert view.message is not None
    assert "连通" in view.message and "装配" in view.message
    service._repo.update_status.assert_awaited_once_with("prov-1", "active")


async def test_test_provider_list_models_auth_error_is_hard_failure(service):
    from agentcore.core.errors import LLMAuthError

    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x"),
            _row(api_key_enc=b"x", status="error"),
        ]
    )
    creds = LLMCredentials(
        api_key="sk-bad", base_url="https://api.deepseek.com", default_model=DEEPSEEK_V4_FLASH
    )
    fake = _FakeProbeProvider(
        list_models_error=LLMAuthError(provider_name="DeepSeek"),
        supports_tools=None,
    )
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert view.status == "error"
    assert "API Key" in (view.message or "")
    assert "装配" not in (view.message or "")
    assert fake.probe_called is False


async def test_test_provider_list_ok_profile_probe_401_blames_combo_model_not_key(
    service,
):
    """/models proved the Key; 模型组合 probe 401 must not say Key 废."""
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x"),
            _row(api_key_enc=b"x", status="error"),
        ]
    )
    service._profiles.list_for_user = AsyncMock(
        return_value=[_profile_row_ref(main_model="DeepSeek1", background_model=None)]
    )
    creds = LLMCredentials(
        api_key="sk-abc",
        base_url="https://api.deepseek.com",
        default_model="",
    )
    fake = _FakeProbeProvider(
        model_ids=[DEEPSEEK_V4_FLASH, "deepseek-chat"],
        probe_error=LLMError(
            "DeepSeek API Key 无效或无权限（鉴权失败），请检查后重试",
            upstream_status=401,
        ),
    )
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert fake.list_models_called is True
    assert fake.probe_called is True
    assert fake.probe_model == "DeepSeek1"
    assert view.status == "error"
    msg = view.message or ""
    assert "模型「DeepSeek1」" in msg
    assert "不被上游接受" in msg
    assert "列出模型" in msg
    assert "装配" in msg
    assert "API Key 无效" not in msg
    assert "连接测试用模型" not in msg


@pytest.mark.parametrize(
    "fake_kw",
    [
        {"list_models_error": LLMError("列出模型失败（HTTP 500）")},
        {"model_ids": []},
    ],
    ids=["soft_error", "empty_list"],
)
async def test_test_provider_list_unproven_without_profile_does_not_probe(
    service, fake_kw
):
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x"),
            _row(api_key_enc=b"x", status="error"),
        ]
    )
    creds = LLMCredentials(
        api_key="sk-abc",
        base_url="https://api.deepseek.com",
        default_model="DeepSeek1",
    )
    fake = _FakeProbeProvider(
        probe_error=LLMError(
            "DeepSeek API Key 无效或无权限（鉴权失败），请检查后重试",
            upstream_status=401,
        ),
        **fake_kw,
    )
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert fake.probe_called is False
    assert view.status == "error"
    assert "未列出模型" in (view.message or "")


@pytest.mark.parametrize(
    "fake_kw",
    [
        {"list_models_error": LLMError("列出模型失败（HTTP 500）")},
        {"model_ids": []},
    ],
    ids=["soft_error", "empty_list"],
)
async def test_test_provider_list_unproven_profile_probe_401_mentions_key_and_model(
    service, fake_kw
):
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x"),
            _row(api_key_enc=b"x", status="error"),
        ]
    )
    service._profiles.list_for_user = AsyncMock(
        return_value=[_profile_row_ref(main_model="DeepSeek1", background_model=None)]
    )
    creds = LLMCredentials(
        api_key="sk-abc",
        base_url="https://api.deepseek.com",
        default_model="",
    )
    fake = _FakeProbeProvider(
        probe_error=LLMError(
            "DeepSeek API Key 无效或无权限（鉴权失败），请检查后重试",
            upstream_status=401,
        ),
        **fake_kw,
    )
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert fake.probe_called is True
    assert view.status == "error"
    msg = view.message or ""
    assert "API Key" in msg
    assert "模型「DeepSeek1」" in msg
    assert "API Key 无效" not in msg
    assert "连接测试用模型" not in msg


class _RaiseProbe:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def probe(self, *, model: str) -> None:
        raise self._exc


@pytest.mark.parametrize(
    ("exc", "keep"),
    [
        (
            LLMInsufficientBalanceError(
                provider_name="DeepSeek",
                upstream_status=401,
            ),
            "余额",
        ),
        (
            LLMError("指定的模型不可用（404）", upstream_status=404),
            "404",
        ),
        (
            LLMError("上游模型服务暂时不可用（500），请稍后再试", upstream_status=500),
            "暂时不可用",
        ),
        (
            LLMError(
                "test model not allowed",
                upstream_status=403,
                upstream_body_preview='{"error":{"code":"model_not_allowed"}}',
            ),
            "model not allowed",
        ),
    ],
    ids=["credits", "404", "5xx", "non_auth_403"],
)
async def test_check_model_reachable_does_not_rewrite_non_auth_probe(exc, keep):
    reach, msg = await check_model_reachable(
        _RaiseProbe(exc),
        model="DeepSeek1",
        model_list=ModelListOutcome(kind="ok", model_ids=(DEEPSEEK_V4_FLASH,)),
    )
    assert reach == "error"
    assert keep in (msg or "")
    assert "不被上游接受" not in (msg or "")
    assert "请核对 API Key 与模型" not in (msg or "")


async def test_test_provider_list_soft_error_without_profile_is_not_green(service):
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x"),
            _row(api_key_enc=b"x", status="error"),
        ]
    )
    creds = LLMCredentials(
        api_key="sk-abc", base_url="https://api.openai.com/v1", default_model="gpt-4o"
    )
    fake = _FakeProbeProvider(
        list_models_error=LLMError("列出模型失败（HTTP 500）"),
        fail=False,
        supports_tools=True,
    )
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert fake.list_models_called is True
    assert fake.probe_called is False
    assert view.status == "error"
    assert "未列出模型" in (view.message or "")


async def test_test_provider_tools_failure_does_not_error_status(service):
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x"),
            _row(api_key_enc=b"x", status="active", supports_tools=None),
        ]
    )
    creds = LLMCredentials(
        api_key="sk-abc", base_url="https://api.openai.com/v1", default_model="gpt-4o"
    )
    fake = _FakeProbeProvider(model_ids=["gpt-4o"], probe_tools_raises=True)
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert view.status == "active"
    assert view.message is not None
    assert "连通" in view.message and "装配" in view.message
    service._repo.update_supports_tools.assert_awaited_once_with("prov-1", None)


async def test_test_provider_logs_probe_failure(service):
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x"),
            _row(api_key_enc=b"x", status="error"),
        ]
    )
    service._profiles.list_for_user = AsyncMock(
        return_value=[_profile_row_ref(background_model=DEEPSEEK_V4_FLASH)]
    )
    creds = LLMCredentials(
        api_key="sk-bad", base_url="https://api.deepseek.com", default_model=""
    )
    fake = _FakeProbeProvider(
        list_models_error=LLMError("no /models"),
        fail=True,
        supports_tools=None,
    )
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
        capture_logs() as caps,
    ):
        view = await service.test_provider("u1", "prov-1")
    assert view.status == "error"
    assert "装配" in (view.message or "")
    failed = next(c for c in caps if c.get("event") == "llm_provider.test.failed")
    assert failed["provider_id"] == "prov-1"
    assert "bad key" in failed["error"] or DEEPSEEK_V4_FLASH in failed["error"]


async def test_test_provider_empty_label_build_without_display_override(service):
    """Display fallback lives in build_provider via credentials.label, not a kwarg."""
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x", label=""),
            _row(api_key_enc=b"x", label="", status="active"),
        ]
    )
    creds = LLMCredentials(
        api_key="sk-abc",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        label=None,
    )
    fake = _FakeProbeProvider(model_ids=["gpt-4o"], supports_tools=None)
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake) as build,
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        await service.test_provider("u1", "prov-1")
    assert "display_name" not in build.call_args.kwargs
    assert build.call_args.args[0].label is None


async def test_test_provider_missing_raises(service):
    service._repo.get = AsyncMock(return_value=None)
    with pytest.raises(NotFoundError):
        await service.test_provider("u1", "missing")


async def test_delete_provider_retargets_main_to_fallback(service):
    service._repo.first_for_user = AsyncMock(
        return_value=_row(id="prov-2", base_url="https://api.openai.com/v1")
    )
    await service.delete_provider("u1", "prov-1")
    service._profiles.retarget_main_provider.assert_awaited_once_with(
        "u1",
        from_provider_id="prov-1",
        to_provider_id="prov-2",
        to_model="gpt-4o",
        to_origin="byok",
    )
    service._profiles.clear_provider_refs.assert_awaited_once_with("u1", "prov-1")


async def test_delete_provider_custom_fallback_keeps_existing_model(service):
    service._repo.first_for_user = AsyncMock(
        return_value=_row(id="prov-2", base_url="https://my-proxy.example/v1")
    )
    await service.delete_provider("u1", "prov-1")
    service._profiles.retarget_main_provider.assert_awaited_once_with(
        "u1",
        from_provider_id="prov-1",
        to_provider_id="prov-2",
        to_model=None,
        to_origin="byok",
    )


async def test_delete_provider_retargets_to_platform_when_last(service):
    service._repo.first_for_user = AsyncMock(return_value=None)
    await service.delete_provider("u1", "prov-1")
    service._profiles.retarget_main_provider.assert_awaited_once_with(
        "u1",
        from_provider_id="prov-1",
        to_provider_id=None,
        to_model=DEEPSEEK_V4_FLASH,
        to_origin="platform",
    )


async def test_delete_provider_missing_raises(service):
    service._repo.delete = AsyncMock(return_value=False)
    with pytest.raises(NotFoundError):
        await service.delete_provider("u1", "missing")


def _profile_row_ref(
    *,
    provider_id: str = "prov-1",
    main_model: str = "gpt-4o",
    background_model: str | None = "gpt5.6",
):
    has_bg = bool((background_model or "").strip())
    return SimpleNamespace(
        main_origin="byok",
        main_provider_id=provider_id,
        main_model=main_model,
        worker_origin=None,
        worker_provider_id=None,
        worker_model=None,
        background_origin="byok" if has_bg else None,
        background_provider_id=provider_id if has_bg else None,
        background_model=background_model if has_bg else None,
        vision_origin=None,
        vision_provider_id=None,
        vision_model=None,
    )


async def test_test_provider_fails_naming_bad_profile_model(service):
    """Connectivity test covers 模型组合 slots — soft-green must not hide a bad ref."""
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x", default_model="gpt-4o"),
            _row(api_key_enc=b"x", status="error", default_model="gpt-4o"),
        ]
    )
    service._profiles.list_for_user = AsyncMock(
        return_value=[_profile_row_ref(background_model="gpt5.6")]
    )
    creds = LLMCredentials(
        api_key="sk-abc", base_url="https://api.openai.com/v1", default_model="gpt-4o"
    )
    fake = _FakeProbeProvider(model_ids=["gpt-4o"], fail_models={"gpt5.6"})
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert view.status == "error"
    assert "gpt5.6" in (view.message or "")
    assert "装配" in (view.message or "")
    assert "gpt5.6" in fake.probe_models
    service._repo.update_status.assert_awaited_once_with("prov-1", "error")


async def test_test_provider_profile_ark_ep_missing_from_list_still_active(service):
    """Ark ep- omitted from /models but chat-ok must not fail the provider test."""
    ep = "ep-20240101000000-abcde"
    service._repo.get = AsyncMock(
        side_effect=[
            _row(api_key_enc=b"x", default_model="gpt-4o"),
            _row(api_key_enc=b"x", status="active", default_model="gpt-4o"),
        ]
    )
    service._profiles.list_for_user = AsyncMock(
        return_value=[_profile_row_ref(background_model=ep)]
    )
    creds = LLMCredentials(
        api_key="sk-abc", base_url="https://ark.example/api/v3", default_model="gpt-4o"
    )
    fake = _FakeProbeProvider(model_ids=["gpt-4o", "doubao-pro"], fail=False)
    with (
        patch(
            "agentcore.llm.provider_service.resolve_provider_credentials",
            AsyncMock(return_value=creds),
        ),
        patch("agentcore.llm.provider_service.build_provider", return_value=fake),
        patch.object(service, "_encryptor", return_value=_enc()),
    ):
        view = await service.test_provider("u1", "prov-1")
    assert view.status == "active"
    assert ep in fake.probe_models
    assert view.message is not None
    assert "连通" in view.message
