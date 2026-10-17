"""Search connection: probe before save, and record a later failure."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.core.errors import ValidationError
from agentcore.core.net import EgressError
from agentcore.tools.builtin.web.cloud_fallback import inference_search_failure_url
from agentcore.tools.builtin.web.provider_service import (
    SearchConnectionService,
    record_provider_search_failure,
)
from agentcore.tools.builtin.web.search_choice import SearchTarget
from agentcore.tools.builtin.web.search_quota import SearchQuotaSnapshot

pytestmark = pytest.mark.anyio

QUOTA = SearchQuotaSnapshot(
    daily_used=1,
    daily_limit=40,
    monthly_used=2,
    monthly_limit=400,
    daily_reset_at="2026-10-17T00:00:00Z",
    monthly_reset_at="2026-11-01T00:00:00Z",
)


def _enc():
    enc = MagicMock()
    enc.encrypt.return_value = b"cipher"
    return enc


@pytest.fixture
def service():
    user = SimpleNamespace(user_id="u1", search_provider_id=None)
    stored: list[SimpleNamespace] = []

    async def _set(_user_id, provider_id, commit=True):
        del commit
        user.search_provider_id = provider_id

    async def _create(**kwargs):
        row = SimpleNamespace(
            id="p1",
            protocol=kwargs["protocol"],
            base_url=kwargs["base_url"],
            api_key_enc=kwargs["api_key_enc"],
            status=kwargs["status"],
            detail=kwargs.get("detail"),
            label=kwargs["label"],
        )
        stored.append(row)
        return row

    svc = SearchConnectionService(MagicMock())
    svc._repo = MagicMock()
    svc._users = MagicMock()
    svc._session.commit = AsyncMock()
    svc._repo.get_by_protocol = AsyncMock(return_value=None)
    svc._repo.create = AsyncMock(side_effect=_create)
    svc._repo.list_for_user = AsyncMock(side_effect=lambda _user_id: list(stored))
    svc._repo.get = AsyncMock(return_value=None)
    svc._repo.mark_needs_reconnect = AsyncMock()
    svc._repo.save_connected = AsyncMock()
    svc._users.get_by_id = AsyncMock(return_value=user)
    svc._users.set_search_provider = AsyncMock(side_effect=_set)
    svc._user = user
    svc._stored = stored
    return svc


def test_inference_failure_url_sits_beside_web_search():
    assert (
        inference_search_failure_url("https://api.example/v1/inference/v1")
        == "https://api.example/v1/inference/search_connection/failure"
    )


async def test_connect_cleversee_rejects_without_writing(service):
    class _Bad:
        def __init__(self, api_key, base_url=None):
            del api_key, base_url

        async def search(self, *args, **kwargs):
            del args, kwargs
            raise EgressError("API Key 不正确。到开析控制台核对或新建")

        async def aclose(self):
            return None

    with (
        patch(
            "agentcore.tools.builtin.web.provider_service.search_encryptor",
            return_value=_enc(),
        ),
        patch(
            "agentcore.tools.builtin.web.provider_service.CleverSeeBackend",
            _Bad,
        ),
        pytest.raises(ValidationError, match="API Key 不正确"),
    ):
        await service.connect_cleversee("u1", "bad-key")
    service._repo.create.assert_not_called()
    service._session.commit.assert_not_called()


async def test_connect_cleversee_saves_and_selects_after_probe(service):
    class _Ok:
        def __init__(self, api_key, base_url=None):
            del api_key, base_url

        async def search(self, *args, **kwargs):
            del args, kwargs
            return []

        async def aclose(self):
            return None

    with (
        patch(
            "agentcore.tools.builtin.web.provider_service.search_encryptor",
            return_value=_enc(),
        ),
        patch(
            "agentcore.tools.builtin.web.provider_service.CleverSeeBackend",
            _Ok,
        ),
        patch(
            "agentcore.tools.builtin.web.provider_service.search_quota_snapshot",
            AsyncMock(return_value=QUOTA),
        ),
        patch(
            "agentcore.tools.builtin.web.provider_service.decrypt_search_key",
            return_value="secret-key-abcd",
        ),
    ):
        view = await service.connect_cleversee("u1", "secret-key-abcd")
    service._repo.create.assert_awaited()
    service._users.set_search_provider.assert_awaited()
    assert view.selected == "cleversee"
    assert view.cleversee is not None
    assert view.cleversee.status == "connected"
    assert view.cleversee.masked_key == "••••abcd"


async def test_mark_selected_failed_keeps_the_selection(service):
    row = SimpleNamespace(id="p1", protocol="cleversee")
    service._user.search_provider_id = "p1"
    service._repo.get = AsyncMock(return_value=row)
    await service.mark_selected_failed("u1", "开析账户欠费")
    service._repo.mark_needs_reconnect.assert_awaited()
    kwargs = service._repo.mark_needs_reconnect.await_args.kwargs
    assert kwargs["detail"] == "开析账户欠费"
    service._users.set_search_provider.assert_not_called()


async def test_proxied_failure_is_not_reported_again():
    target = SearchTarget(
        kind="provider",
        protocol="cleversee",
        provider_id="p1",
        proxy=True,
    )
    with (
        patch(
            "agentcore.tools.builtin.web.provider_service.get_inference_search_credentials",
            return_value=SimpleNamespace(),
        ),
        patch(
            "agentcore.tools.builtin.web.provider_service.note_own_search_failure",
            AsyncMock(),
        ) as note,
        patch(
            "agentcore.tools.builtin.web.provider_service.report_search_connection_failure",
            AsyncMock(),
        ) as report,
    ):
        await record_provider_search_failure(target, "u1", "开析账户欠费")
    note.assert_not_called()
    report.assert_not_called()
