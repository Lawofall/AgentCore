"""Per-worker 模型覆盖（编排器权威段）：schema 形状 / 空跟槽 / RunSpec / resolve / extras."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentcore.llm.catalog import ModelCatalog, ModelCatalogCurrent, ModelCatalogEntry
from agentcore.llm.profiles import PLATFORM_PROVIDER_SENTINEL, TurnProfiles
from agentcore.runtime.costing import ROLE_MEMBER, resolve_run_models
from agentcore.runtime.debate.models import ModelIdentity
from agentcore.runtime.delegate.task_models import (
    TASK_MODEL_SCHEMA_PROPS,
    identity_from_task_item,
    inherit_model_from_tool,
    prepare_task_model_fields,
)
from agentcore.runtime.runs.builder import build_run_plan
from agentcore.tools.builtin.delegate.schema import DELEGATE_PARAMETERS
from agentcore.tools.builtin.replan import _REPLAN_PARAMETERS


def _entry(
    mid: str,
    *,
    origin: str = "platform",
    provider_id: str | None = None,
    available: bool = True,
) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id=mid,
        origin=origin,  # type: ignore[arg-type]
        display_name=mid,
        vendor="test",
        available=available,
        provider_id=provider_id,
    )


def _catalog(*entries: ModelCatalogEntry) -> ModelCatalog:
    return ModelCatalog(
        current=ModelCatalogCurrent(id="x", origin="platform"),
        byok_configured=True,
        models=list(entries),
    )


def test_schema_exposes_single_model_field_on_tasks_and_replan():
    task_props = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]
    assert "model" in task_props
    assert "origin" not in task_props and "provider_id" not in task_props
    assert "@platform" in str(task_props["model"].get("description", ""))
    bind_props = _REPLAN_PARAMETERS["properties"]["binds"]["items"]["properties"]
    add_props = _REPLAN_PARAMETERS["properties"]["add"]["items"]["properties"]
    for props in (bind_props, add_props):
        assert set(TASK_MODEL_SCHEMA_PROPS).issubset(props)
        assert "origin" not in props


@pytest.mark.asyncio
async def test_prepare_empty_follows_slot():
    items = [{"role": "A", "task": "do", "model": "", "origin": ""}]
    errors, idents = await prepare_task_model_fields(
        items, user_id="u1", catalog=_catalog(_entry("glm-5.2"))
    )
    assert errors == []
    assert idents == []
    assert items[0].get("model", "") in ("", None) or not str(items[0].get("model")).strip()


@pytest.mark.asyncio
async def test_prepare_bare_model_resolves_unique_catalog_hit():
    items = [{"role": "A", "task": "do", "model": "glm-5.2"}]
    errors, idents = await prepare_task_model_fields(
        items, user_id="u1", catalog=_catalog(_entry("glm-5.2"))
    )
    assert errors == []
    assert len(idents) == 1
    assert items[0]["model"] == f"{PLATFORM_PROVIDER_SENTINEL}/glm-5.2"


@pytest.mark.asyncio
async def test_prepare_rejects_unprefixed_route_key_as_unknown_mention():
    items = [{"role": "A", "task": "do", "model": "platform/glm-5.2"}]
    errors, _ = await prepare_task_model_fields(
        items, user_id="u1", catalog=_catalog(_entry("glm-5.2"))
    )
    assert errors
    assert "@platform/glm-5.2" in errors[0]
    assert "platform/platform" not in errors[0]


@pytest.mark.asyncio
async def test_prepare_at_ref_encodes_route_key():
    items = [{"role": "A", "task": "do", "model": "@platform/glm-5.2"}]
    errors, idents = await prepare_task_model_fields(
        items, user_id="u1", catalog=_catalog(_entry("glm-5.2"))
    )
    assert errors == []
    assert items[0]["model"] == f"{PLATFORM_PROVIDER_SENTINEL}/glm-5.2"


@pytest.mark.asyncio
async def test_prepare_byok_mention_ambiguous_without_provider():
    items = [{"role": "A", "task": "do", "model": "deepseek-chat"}]
    errors, _ = await prepare_task_model_fields(
        items,
        user_id="u1",
        catalog=_catalog(
            _entry("deepseek-chat", origin="byok", provider_id="p1"),
            _entry("deepseek-chat", origin="byok", provider_id="p2"),
        ),
    )
    assert errors
    assert "@byok/" in errors[0]


@pytest.mark.asyncio
async def test_prepare_rejects_catalog_miss():
    items = [{"role": "A", "task": "do", "model": "no-such", "origin": "platform"}]
    errors, _ = await prepare_task_model_fields(
        items, user_id="u1", catalog=_catalog(_entry("glm-5.2"))
    )
    assert errors and "目录未命中" in errors[0]


@pytest.mark.asyncio
async def test_prepare_encodes_route_key_into_run_spec():
    items = [
        {"role": "A", "task": "a", "model": "glm-5.2", "origin": "platform"},
        {
            "role": "B",
            "task": "b",
            "model": "deepseek-chat",
            "origin": "byok",
            "provider_id": "prov-1",
        },
        {"role": "C", "task": "c"},
    ]
    catalog = _catalog(
        _entry("glm-5.2"),
        _entry("deepseek-chat", origin="byok", provider_id="prov-1"),
    )
    errors, idents = await prepare_task_model_fields(
        items, user_id="u1", catalog=catalog
    )
    assert errors == []
    assert len(idents) == 2
    assert items[0]["model"] == f"{PLATFORM_PROVIDER_SENTINEL}/glm-5.2"
    assert items[1]["model"] == "prov-1/deepseek-chat"
    assert "origin" not in items[0]
    plan, plan_errs = build_run_plan(items, id_prefix="t")
    assert plan_errs == []
    assert plan.nodes[0].model == f"{PLATFORM_PROVIDER_SENTINEL}/glm-5.2"
    assert plan.nodes[1].model == "prov-1/deepseek-chat"
    assert plan.nodes[2].model == ""


def test_resolve_run_models_member_prefers_spec_model():
    profiles = TurnProfiles(
        model="main-pro",
        model_overrides={"agent": "worker-flash"},
    )
    priced, request = resolve_run_models(profiles, "", cost_role=ROLE_MEMBER)
    assert priced == "worker-flash"
    assert request == "worker-flash"
    priced_x, request_x = resolve_run_models(
        profiles, "platform/glm-5.2", cost_role=ROLE_MEMBER
    )
    assert priced_x == "glm-5.2"
    assert request_x == "platform/glm-5.2"


@pytest.mark.asyncio
async def test_prepare_continue_from_inherits_prior_route_key():
    items = [
        {
            "role": "A",
            "task": "revise",
            "continue_from_run_id": "del_old_1",
        }
    ]
    errors, idents = await prepare_task_model_fields(
        items,
        user_id="u1",
        catalog=_catalog(_entry("glm-5.2")),
        inherit_model=lambda _rid: "platform/glm-5.2",
    )
    assert errors == []
    assert idents == []
    assert items[0]["model"] == "platform/glm-5.2"


def test_inherit_model_from_tool_reads_session_spec():
    tool = SimpleNamespace(
        _session_store=SimpleNamespace(
            get=lambda _rid: SimpleNamespace(
                spec=SimpleNamespace(model="platform/glm-5.2")
            )
        ),
        _last_graph_plan=None,
        _last_graph_seed=None,
    )
    assert inherit_model_from_tool(tool, "r1") == "platform/glm-5.2"


@pytest.mark.asyncio
async def test_ensure_delegate_route_extras_registers_cross_provider(monkeypatch):
    from agentcore.runtime.delegate import task_models as tm

    called: dict[str, object] = {}

    async def _fake(llm, identities, *, user_id=None):
        called["llm"] = llm
        called["idents"] = list(identities)
        called["user_id"] = user_id

    monkeypatch.setattr(tm, "ensure_debate_route_extras", _fake)
    llm = object()
    idents = [
        ModelIdentity(model="glm-5.2", origin="platform"),
        ModelIdentity(model="deepseek-chat", origin="byok", provider_id="prov-1"),
    ]
    await tm.ensure_delegate_route_extras(llm, idents, user_id="u1")
    assert called["user_id"] == "u1"
    assert len(called["idents"]) == 2  # type: ignore[arg-type]


def test_identity_from_task_item_normalizes_platform():
    ident = identity_from_task_item(
        {"model": " glm-5.2 ", "origin": "PLATFORM", "provider_id": "should-clear"}
    )
    assert ident.model == "glm-5.2"
    assert ident.origin == "platform"
    assert ident.provider_id == ""
    assert ident.route_key() == f"{PLATFORM_PROVIDER_SENTINEL}/glm-5.2"


def test_identity_from_task_item_at_ref():
    ident = identity_from_task_item({"model": "@byok/prov-1/openai/gpt-4o"})
    assert ident.model == "openai/gpt-4o"
    assert ident.origin == "byok"
    assert ident.provider_id == "prov-1"
