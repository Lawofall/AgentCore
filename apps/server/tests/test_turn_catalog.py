"""Turn catalog: ticketed sidecar reads GET /v1/account/models; same-model skips it."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentcore.account.credentials import (
    AccountCloudError,
    AccountCredentials,
    account_credentials_scope,
)
from agentcore.api.routes.model_catalog import to_model_catalog_response
from agentcore.llm.catalog import ModelCatalog, ModelCatalogCurrent, ModelCatalogEntry
from agentcore.llm.turn_catalog import (
    DEBATE_CATALOG_UNAVAILABLE,
    DELEGATE_CATALOG_UNAVAILABLE,
    TurnCatalogUnavailableError,
    catalog_from_wire,
    load_turn_model_catalog,
)
from agentcore.runtime.debate.models import debate_needs_model_catalog
from agentcore.runtime.debate.types import DebateConfig, DebateForm, DebateSide
from agentcore.runtime.delegate.task_models import (
    items_need_model_catalog,
    load_catalog_for_items,
)


def _entry(
    model_id: str, *, origin: str = "platform", provider_id: str | None = None
) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id=model_id,
        origin=origin,  # type: ignore[arg-type]
        display_name=model_id,
        vendor="v",
        available=True,
        provider_id=provider_id,
    )


def _catalog(*rows: ModelCatalogEntry) -> ModelCatalog:
    current = rows[0]
    return ModelCatalog(
        current=ModelCatalogCurrent(
            id=current.id, origin=current.origin, provider_id=current.provider_id
        ),
        byok_configured=any(row.origin == "byok" for row in rows),
        models=list(rows),
    )


def _creds() -> AccountCredentials:
    return AccountCredentials(api_key="acct-tok", base_url="https://example.test/v1/account")


def test_catalog_from_wire_roundtrip() -> None:
    catalog = _catalog(_entry("plat-a"), _entry("plat-b"))
    restored = catalog_from_wire(to_model_catalog_response(catalog).model_dump())
    assert [row.id for row in restored.models] == ["plat-a", "plat-b"]
    assert restored.current.id == "plat-a"
    assert restored.models[0].origin == "platform"
    assert restored.models[0].available is True


def test_debate_needs_catalog_only_when_matchup_or_names() -> None:
    sides = [
        DebateSide(key="a", name="正", stance="支持"),
        DebateSide(key="b", name="反", stance="反对"),
    ]
    cfg = DebateConfig(motion="m", form=DebateForm.DEBATE, sides=sides)
    assert debate_needs_model_catalog(cfg, cross_model=False) is False
    assert debate_needs_model_catalog(cfg, cross_model=True) is True
    named = [
        DebateSide(key="a", name="正", stance="支持", model="gpt-4o"),
        sides[1],
    ]
    named_cfg = DebateConfig(motion="m", form=DebateForm.DEBATE, sides=named)
    assert debate_needs_model_catalog(named_cfg, cross_model=False) is True


def test_items_need_catalog_when_a_task_names_a_model() -> None:
    assert items_need_model_catalog([{"model": ""}]) is False
    assert items_need_model_catalog([{"model": "@platform/glm-5.2"}]) is True


@pytest.mark.asyncio
async def test_ticketed_turn_loads_catalog_over_http_not_postgres(monkeypatch) -> None:
    catalog = _catalog(_entry("plat-a"))
    payload = to_model_catalog_response(catalog).model_dump()

    async def fake_list(creds: AccountCredentials) -> dict:
        assert creds.api_key == "acct-tok"
        return payload

    def boom() -> None:
        raise AssertionError("local postgres")

    monkeypatch.setattr("agentcore.account.credentials.cloud_list_models", fake_list)
    monkeypatch.setattr("agentcore.db.base.async_session_factory", boom)
    with account_credentials_scope(_creds()):
        loaded = await load_turn_model_catalog("user-1")
    assert loaded.models[0].id == "plat-a"


@pytest.mark.asyncio
async def test_ticketed_http_failure_does_not_open_postgres(monkeypatch) -> None:
    async def fake_list(_creds: AccountCredentials) -> dict:
        raise AccountCloudError("down")

    def boom() -> None:
        raise AssertionError("local postgres")

    monkeypatch.setattr("agentcore.account.credentials.cloud_list_models", fake_list)
    monkeypatch.setattr("agentcore.db.base.async_session_factory", boom)
    with (
        account_credentials_scope(_creds()),
        pytest.raises(TurnCatalogUnavailableError, match="account_http"),
    ):
        await load_turn_model_catalog("user-1")


@pytest.mark.asyncio
async def test_delegate_named_model_on_sidecar_reports_catalog_miss(monkeypatch) -> None:
    async def fake_list(_creds: AccountCredentials) -> dict:
        raise AccountCloudError("down")

    monkeypatch.setattr("agentcore.account.credentials.cloud_list_models", fake_list)
    with account_credentials_scope(_creds()):
        catalog, err = await load_catalog_for_items(
            [{"model": "@platform/glm-5.2"}], user_id="user-1"
        )
    assert catalog is None
    assert err == DELEGATE_CATALOG_UNAVAILABLE
    assert "稍后重试" not in err


@pytest.mark.asyncio
async def test_list_account_models_uses_resolver(monkeypatch) -> None:
    from agentcore.api.routes.account import list_account_models

    catalog = _catalog(_entry("plat-a"))

    async def resolve(session: object, user_id: str) -> ModelCatalog:
        assert session == "db"
        assert user_id == "u-1"
        return catalog

    monkeypatch.setattr("agentcore.llm.catalog.resolve_model_catalog", resolve)
    resp = await list_account_models(SimpleNamespace(user_id="u-1"), "db")  # type: ignore[arg-type]
    assert resp.models[0].id == "plat-a"


def _debate_tool(ctx):  # noqa: ANN001
    from agentcore.runtime.events import EventSink
    from agentcore.tools.builtin.debate.tool import DebateTool
    from agentcore.tools.registry import ToolRegistry
    from tests.delegate.conftest import Provider

    return DebateTool(
        llm=Provider([]),
        sink=EventSink(),
        system_prompt="sys",
        user_message="开辩",
        tools=ToolRegistry(),
        base_tool_context=ctx,
        conversation_id="conv",
        message_id="m",
        captain_run_id="captain",
        approval_gate=None,
    )


def _debate_ctx():
    from agentcore.tools.protocol import ToolContext
    from agentcore.tools.sandbox.subprocess import SubprocessSandbox
    from agentcore.workspace.server import ServerWorkspace

    backend = ServerWorkspace(
        root=Path(tempfile.mkdtemp(prefix="turn_cat_")),
        sandbox=SubprocessSandbox(),
    )
    return ToolContext.create(
        execution_id="e",
        run_id="captain",
        agent_id="CEO",
        backend=backend,
        user_id="u",
        conversation_id="conv",
    )


def _sides() -> list[dict[str, str]]:
    return [
        {"key": "pro", "name": "正方", "stance": "支持换许可"},
        {"key": "con", "name": "反方", "stance": "反对换许可"},
    ]


@pytest.mark.asyncio
async def test_same_model_debate_skips_catalog(monkeypatch) -> None:
    from agentcore.tools.builtin.debate.tool import DebateTool
    from agentcore.tools.protocol import ToolEffect, ToolResult

    fetched = {"n": 0}

    async def fake_list(_creds: AccountCredentials) -> dict:
        fetched["n"] += 1
        raise AssertionError("same-model must not fetch")

    def boom() -> None:
        raise AssertionError("local postgres")

    async def fake_run(self, config, usage_metadata):  # noqa: ANN001
        return ToolResult(
            tool_call_id="",
            success=True,
            output="ok",
            effect=ToolEffect.CONTINUE,
        )

    monkeypatch.setattr("agentcore.account.credentials.cloud_list_models", fake_list)
    monkeypatch.setattr("agentcore.db.base.async_session_factory", boom)
    monkeypatch.setattr(DebateTool, "_run_moderator", fake_run)
    ctx = _debate_ctx()
    tool = _debate_tool(ctx)
    with account_credentials_scope(_creds()):
        result = await tool.execute(
            {"motion": "要不要换 OSI", "sides": _sides()},
            ctx,
        )
    assert result.success is True
    assert fetched["n"] == 0


@pytest.mark.asyncio
async def test_cross_model_on_sidecar_fails_closed(monkeypatch) -> None:
    async def fake_list(_creds: AccountCredentials) -> dict:
        raise AccountCloudError("down")

    def boom() -> None:
        raise AssertionError("local postgres")

    monkeypatch.setattr("agentcore.account.credentials.cloud_list_models", fake_list)
    monkeypatch.setattr("agentcore.db.base.async_session_factory", boom)
    ctx = _debate_ctx()
    tool = _debate_tool(ctx)
    with account_credentials_scope(_creds()):
        result = await tool.execute(
            {"motion": "要不要换 OSI", "sides": _sides(), "cross_model": True},
            ctx,
        )
    assert result.success is False
    assert result.error == DEBATE_CATALOG_UNAVAILABLE
    assert "稍后重试" not in (result.error or "")


@pytest.mark.asyncio
async def test_cross_model_on_sidecar_uses_fetched_matchup(monkeypatch) -> None:
    from agentcore.tools.builtin.debate.tool import DebateTool
    from agentcore.tools.protocol import ToolEffect, ToolResult

    catalog = _catalog(_entry("plat-a"), _entry("plat-b"))
    payload = to_model_catalog_response(catalog).model_dump()

    async def fake_list(_creds: AccountCredentials) -> dict:
        return payload

    def boom() -> None:
        raise AssertionError("local postgres")

    seen: dict[str, str] = {}

    async def fake_run(self, config, usage_metadata):  # noqa: ANN001
        seen["a"] = config.sides[0].model
        seen["b"] = config.sides[1].model
        return ToolResult(
            tool_call_id="",
            success=True,
            output="ok",
            effect=ToolEffect.CONTINUE,
        )

    monkeypatch.setattr("agentcore.account.credentials.cloud_list_models", fake_list)
    monkeypatch.setattr("agentcore.db.base.async_session_factory", boom)
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_model_allowlist",
        lambda: ["plat-a", "plat-b"],
    )
    monkeypatch.setattr(DebateTool, "_run_moderator", fake_run)
    ctx = _debate_ctx()
    tool = _debate_tool(ctx)
    with account_credentials_scope(_creds()):
        result = await tool.execute(
            {"motion": "要不要换 OSI", "sides": _sides(), "cross_model": True},
            ctx,
        )
    assert result.success is True
    assert seen == {"a": "plat-a", "b": "plat-b"}
