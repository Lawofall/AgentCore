"""Search connection: probe before save, and record a later failure."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentcore.core.errors import ValidationError
from agentcore.search.choice import SearchTarget
from agentcore.search.connection import (
    SearchConnectionService,
    record_provider_search_failure,
)
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
            source=kwargs.get("source", "provider"),
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


async def test_connect_from_provider_saves_without_pasted_key(service):
    from agentcore.llm.provider_service import OfficialDeepSeekProvider

    class _Ok:
        def __init__(self, api_key, base_url=None):
            self.api_key = api_key
            del base_url

        async def search(self, *args, **kwargs):
            del args, kwargs
            return []

        async def aclose(self):
            return None

    with (
        patch(
            "agentcore.search.connection.load_official_deepseek_provider",
            AsyncMock(
                return_value=OfficialDeepSeekProvider(
                    provider_id="llm-p",
                    api_key="sk-model",
                    label="DeepSeek",
                )
            ),
        ),
        patch(
            "agentcore.search.connection.DeepSeekBackend",
            _Ok,
        ),
        patch(
            "agentcore.search.connection.search_quota_snapshot",
            AsyncMock(return_value=QUOTA),
        ),
        patch(
            "agentcore.search.connection.official_deepseek_provider_label",
            AsyncMock(return_value="DeepSeek"),
        ),
    ):
        view = await service.connect_deepseek("u1")
    kwargs = service._repo.create.await_args.kwargs
    assert kwargs["source"] == "provider"
    assert kwargs["api_key_enc"] is None
    assert view.selected == "deepseek"
    assert view.deepseek is not None
    assert view.deepseek.source == "provider"
    assert view.deepseek.masked_key is None
    assert view.official_provider_label == "DeepSeek"


async def test_connect_from_provider_rejects_without_service(service):
    with (
        patch(
            "agentcore.search.connection.load_official_deepseek_provider",
            AsyncMock(return_value=None),
        ),
        pytest.raises(ValidationError, match="还没有连接官方 DeepSeek 服务商"),
    ):
        await service.connect_deepseek("u1")
    service._repo.create.assert_not_called()
    service._session.commit.assert_not_called()


async def test_mark_selected_failed_keeps_the_selection(service):
    row = SimpleNamespace(id="p1", protocol="deepseek")
    service._user.search_provider_id = "p1"
    service._repo.get = AsyncMock(return_value=row)
    await service.mark_selected_failed("u1", "DeepSeek 账户余额不足")
    service._repo.mark_needs_reconnect.assert_awaited()
    kwargs = service._repo.mark_needs_reconnect.await_args.kwargs
    assert kwargs["detail"] == "DeepSeek 账户余额不足"
    service._users.set_search_provider.assert_not_called()


async def test_proxied_failure_is_not_reported_again():
    target = SearchTarget(
        kind="provider",
        protocol="deepseek",
        provider_id="p1",
    )
    with (
        patch(
            "agentcore.search.connection.get_inference_search_credentials",
            return_value=SimpleNamespace(),
        ),
        patch(
            "agentcore.search.connection.note_own_search_failure",
            AsyncMock(),
        ) as note,
    ):
        await record_provider_search_failure(target, "u1", "DeepSeek 账户余额不足")
    note.assert_not_called()
