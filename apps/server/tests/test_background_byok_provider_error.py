"""Background chrome: mark BYOK provider error on config-shaped failures only."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.core.errors import (
    LLMAuthError,
    LLMError,
    LLMInvalidResponseError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from agentcore.llm.background_failure import is_config_shaped_background_failure
from agentcore.llm.credentials import LLMCredentials


def test_config_shaped_allowlist():
    assert is_config_shaped_background_failure(LLMInvalidResponseError("html"))
    assert is_config_shaped_background_failure(LLMAuthError(provider_name="user"))
    assert is_config_shaped_background_failure(LLMError("model gone", upstream_status=404))


def test_config_shaped_excludes_retryable_and_non_config():
    assert not is_config_shaped_background_failure(LLMTimeoutError("t"))
    assert not is_config_shaped_background_failure(LLMUpstreamError("503", upstream_status=503))
    assert not is_config_shaped_background_failure(TimeoutError())
    assert not is_config_shaped_background_failure(
        LLMError("context overflow", upstream_status=413)
    )
    assert not is_config_shaped_background_failure(RuntimeError("network down"))


class _SessionCM:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *_a):
        return False


def _patch_gate_and_repo(monkeypatch, *, update_status: AsyncMock):
    class _Repo:
        def __init__(self, _session):
            self.update_status = update_status

    monkeypatch.setattr("agentcore.billing.gate.async_session_factory", lambda: _SessionCM())
    monkeypatch.setattr(
        "agentcore.billing.gate.UserLlmProviderRepository",
        _Repo,
    )


@pytest.mark.asyncio
async def test_mark_on_non_retryable_config_failure(monkeypatch):
    from agentcore.billing.gate import BackgroundGateResolve, run_background_llm

    byok = LLMCredentials(
        api_key="sk-user",
        base_url="https://user.example/v1",
        default_model="user-flash",
        source="user",
        provider_id="p1",
    )
    update_status = AsyncMock()
    _patch_gate_and_repo(monkeypatch, update_status=update_status)
    monkeypatch.setattr(
        "agentcore.billing.gate.resolve_and_gate_background",
        AsyncMock(return_value=BackgroundGateResolve(credentials=byok)),
    )

    async def _runner(_creds: LLMCredentials) -> str:
        raise LLMInvalidResponseError("gateway login html")

    with pytest.raises(LLMInvalidResponseError):
        await run_background_llm("u1", purpose="title", runner=_runner)

    update_status.assert_awaited_once_with("p1", "error")


@pytest.mark.asyncio
async def test_no_mark_on_timeout(monkeypatch):
    from agentcore.billing.gate import BackgroundGateResolve, run_background_llm

    byok = LLMCredentials(
        api_key="sk-user",
        base_url="https://user.example/v1",
        default_model="user-flash",
        source="user",
        provider_id="p1",
    )
    update_status = AsyncMock()
    _patch_gate_and_repo(monkeypatch, update_status=update_status)
    monkeypatch.setattr(
        "agentcore.billing.gate.resolve_and_gate_background",
        AsyncMock(return_value=BackgroundGateResolve(credentials=byok)),
    )

    async def _runner(_creds: LLMCredentials) -> str:
        raise LLMTimeoutError("upstream timeout")

    with pytest.raises(LLMTimeoutError):
        await run_background_llm("u1", purpose="memory", runner=_runner)

    update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_mark_on_platform_credentials(monkeypatch):
    from agentcore.billing.gate import BackgroundGateResolve, run_background_llm

    platform = LLMCredentials(
        api_key="sk-platform",
        base_url="https://p.example/v1",
        default_model="flash",
        source="platform",
    )
    update_status = AsyncMock()
    _patch_gate_and_repo(monkeypatch, update_status=update_status)
    monkeypatch.setattr(
        "agentcore.billing.gate.resolve_and_gate_background",
        AsyncMock(return_value=BackgroundGateResolve(credentials=platform)),
    )

    async def _runner(_creds: LLMCredentials) -> str:
        raise LLMInvalidResponseError("platform gateway html")

    with pytest.raises(LLMInvalidResponseError):
        await run_background_llm("u1", purpose="title", runner=_runner)

    update_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_db_failure_is_swallowed(monkeypatch):
    from agentcore.billing.gate import maybe_mark_byok_provider_error

    byok = LLMCredentials(
        api_key="sk-user",
        base_url="https://user.example/v1",
        source="user",
        provider_id="p1",
    )

    class _Repo:
        def __init__(self, _session):
            pass

        async def update_status(self, *_a, **_k):
            raise RuntimeError("db down")

    monkeypatch.setattr(
        "agentcore.billing.gate.async_session_factory",
        lambda: _SessionCM(),
    )
    monkeypatch.setattr(
        "agentcore.billing.gate.UserLlmProviderRepository",
        _Repo,
    )

    await maybe_mark_byok_provider_error(
        user_id="u1",
        purpose="title",
        credentials=byok,
        exc=LLMAuthError(provider_name="user"),
    )
