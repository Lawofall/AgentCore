"""Ticketed sidecar must not open local Postgres session factories."""

from __future__ import annotations

import pytest

from agentcore.account.credentials import AccountCredentials, account_credentials_scope
from agentcore.db.base import async_session_factory, telemetry_session_factory
from agentcore.db.errors import SidecarLocalDbForbiddenError, is_db_connectivity_error
from agentcore.folders.credentials import FoldersCredentials, folders_credentials_scope


def _folders() -> FoldersCredentials:
    return FoldersCredentials(
        api_key="tok", base_url="https://api.example.com/v1/folders"
    )


def _account() -> AccountCredentials:
    return AccountCredentials(
        api_key="tok", base_url="https://api.example.com/v1/account"
    )


def test_sidecar_local_db_forbidden_is_not_connectivity():
    err = SidecarLocalDbForbiddenError("ticketed sidecar must not open local Postgres")
    assert is_db_connectivity_error(err) is False


@pytest.mark.asyncio
async def test_primary_factory_refuses_when_folders_ticket_bound():
    with (
        folders_credentials_scope(_folders()),
        pytest.raises(SidecarLocalDbForbiddenError),
    ):
        async_session_factory()


@pytest.mark.asyncio
async def test_primary_factory_refuses_when_account_ticket_bound():
    with (
        account_credentials_scope(_account()),
        pytest.raises(SidecarLocalDbForbiddenError),
    ):
        async_session_factory()


@pytest.mark.asyncio
async def test_telemetry_factory_refuses_when_folders_ticket_bound():
    with (
        folders_credentials_scope(_folders()),
        pytest.raises(SidecarLocalDbForbiddenError),
    ):
        telemetry_session_factory()


@pytest.mark.asyncio
async def test_factory_allows_checkout_without_tickets():
    # Opening the CM must not raise the contract error. Connecting may still fail
    # in unit tests without Postgres — that is a different class of error.
    cm = async_session_factory()
    try:
        await cm.__aenter__()
    except SidecarLocalDbForbiddenError:
        raise
    except Exception:
        pass
    else:
        await cm.__aexit__(None, None, None)
