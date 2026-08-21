"""Integration tests for BYOK 多服务商 management + the billing preflight.

Auto-skips (via the shared ``client`` fixture) when no PostgreSQL is reachable.
Covers the /users/me/llm-providers routes (list / create / update / delete /
connectivity-test / defaults), at-rest encryption + last-4 masking, the same-model-
id-across-two-providers catalog, delete-provider fallback, and the turn preflight
that refuses a keyless BYOK turn (402) while a keyed BYOK turn skips the platform
quota gate.

The connectivity probe and the chat stream are stubbed so no real DeepSeek call is
made: the /test route's provider is monkeypatched, and "skips quota" is asserted
against the preflight gate directly rather than by opening a stream.
"""

import pytest

from agentcore.api.routes.conversations._helpers import _preflight_turn_llm
from agentcore.config import settings
from agentcore.core.errors import LLMError, QuotaExceededError
from agentcore.core.types import new_id
from agentcore.db.repositories import (
    ConversationRepository,
    CostEventRepository,
    UserLlmProviderRepository,
    UserRepository,
)
from agentcore.llm.provider_service import LlmProviderService
from agentcore.llm.tools_gate import TOOLS_SOFT_GATE_WARNING
from tests.integration.conftest import register_and_login

_MASTER_KEY = "a" * 64
_OVER_MONTHLY_NANO = 20_000_000_000  # above the default ¥10 monthly cap
_BASE = "/v1/users/me/llm-providers"


async def _make_conversation(session_factory, *, user_id: str) -> str:
    async with session_factory() as session:
        conv = await ConversationRepository(session).create(user_id=user_id, title="t")
        return conv.id


def _run(run_id: str, *, total: int) -> dict:
    return {
        "run_id": run_id,
        "parent_run_id": None,
        "agent_id": run_id,
        "role": "captain",
        "model": "deepseek-v4-pro",
        "tokens": {"input": 100, "output": 50, "reasoning": 0, "cache_hit": 0, "cache_miss": 100},
        "cost": {"input": 0, "cached": 0, "output": total, "total": total},
        "cost_total_nano": total,
        "currency": "USD",
        "rounds": 1,
        "duration_ms": 1,
    }


async def _seed_spend(session_factory, *, user_id: str, conversation_id: str, total: int) -> None:
    async with session_factory() as session:
        await CostEventRepository(session).record_runs(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=new_id(),
            runs=[_run(new_id(), total=total)],
        )


async def _add_provider(session_factory, *, user_id: str, api_key: str, label: str = "DeepSeek"):
    async with session_factory() as session:
        return await LlmProviderService(session).create_provider(
            user_id, label=label, api_key=api_key
        )


@pytest.fixture
def byok(monkeypatch):
    """BYOK billing + a valid master key configured (auto-restored)."""
    monkeypatch.setattr(settings, "billing_mode", "byok")
    monkeypatch.setattr(settings, "encryption_key", _MASTER_KEY)


# --- auth gate ---


async def test_llm_provider_routes_require_auth(client):
    assert (await client.get(_BASE)).status_code == 401
    assert (await client.post(_BASE, json={"api_key": "x"})).status_code == 401
    assert (await client.delete(f"{_BASE}/xyz")).status_code == 401
    assert (await client.post(f"{_BASE}/xyz/test")).status_code == 401


# --- list / create / mask lifecycle ---


async def test_list_empty(client, byok):
    await register_and_login(client, "provuser1")
    body = (await client.get(_BASE)).json()
    assert body["providers"] == []
    assert "default_chat" not in body
    assert body["billing_mode"] == "byok"


async def test_create_provider_masks_and_seeds_profile(client, byok):
    await register_and_login(client, "provuser2")
    r = await client.post(
        _BASE,
        json={
            "label": "OpenAI",
            "api_key": "sk-openai-abcd1234",
            "base_url": "https://api.openai.com/v1",
            "default_model": "gpt-4o",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["label"] == "OpenAI"
    assert body["status"] == "unchecked"
    assert body["masked_key"] == "••••1234"
    assert body["base_url"] == "https://api.openai.com/v1"
    assert body["default_model"] == "gpt-4o"
    assert "is_default_chat" not in body

    listed = (await client.get(_BASE)).json()
    assert len(listed["providers"]) == 1
    assert listed["default_model_profile_id"] is not None

    profiles = (await client.get("/v1/users/me/llm-model-profiles")).json()
    assert any(p["name"] == "当前配置" and p["is_default"] for p in profiles["data"])
    default = next(p for p in profiles["data"] if p["is_default"])
    assert default["main"]["provider_id"] == body["id"]
    assert default["main"]["model"] == "gpt-4o"


async def test_create_provider_requires_api_key(client, byok):
    await register_and_login(client, "provuser3")
    r = await client.post(_BASE, json={"label": "X", "default_model": "gpt-4o"})
    assert r.status_code == 422, r.text


async def test_create_provider_refused_without_master_key(client, monkeypatch):
    monkeypatch.setattr(settings, "billing_mode", "byok")
    monkeypatch.setattr(settings, "encryption_key", "")  # no master key → can't store
    await register_and_login(client, "provuser4")
    r = await client.post(_BASE, json={"api_key": "sk-x"})
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "KEY_STORAGE_UNAVAILABLE"


async def test_update_provider_keeps_ciphertext_and_changes_model(client, byok):
    await register_and_login(client, "provuser5")
    created = (
        await client.post(
            _BASE,
            json={
                "api_key": "sk-keep-me-4242",
                "base_url": "https://api.deepseek.com",
                "default_model": "deepseek-v4-flash",
            },
        )
    ).json()
    pid = created["id"]

    r = await client.patch(
        f"{_BASE}/{pid}",
        json={"default_model": "deepseek-v4-pro"},  # api_key omitted → keep ciphertext
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["masked_key"] == "••••4242"
    assert body["default_model"] == "deepseek-v4-pro"


async def test_multiple_providers_and_delete_retargets_profile(client, byok):
    await register_and_login(client, "provuser7")
    a = (await client.post(_BASE, json={"api_key": "sk-a-1111", "label": "A"})).json()
    b = (await client.post(_BASE, json={"api_key": "sk-b-2222", "label": "B"})).json()
    listed = (await client.get(_BASE)).json()
    assert {p["label"] for p in listed["providers"]} == {"A", "B"}
    profile_id = listed["default_model_profile_id"]
    assert profile_id is not None

    profiles = (await client.get("/v1/users/me/llm-model-profiles")).json()
    default = next(p for p in profiles["data"] if p["id"] == profile_id)
    assert default["main"]["provider_id"] == a["id"]

    # Delete the default provider → profile main retargets to the survivor.
    r = await client.delete(f"{_BASE}/{a['id']}")
    assert r.status_code == 200, r.text
    listed = (await client.get(_BASE)).json()
    assert [p["id"] for p in listed["providers"]] == [b["id"]]
    profiles = (await client.get("/v1/users/me/llm-model-profiles")).json()
    default = next(p for p in profiles["data"] if p["id"] == profile_id)
    assert default["main"]["provider_id"] == b["id"]


async def test_model_profile_crud_and_set_default(client, byok):
    await register_and_login(client, "provuser8")
    a = (await client.post(_BASE, json={"api_key": "sk-a-1111", "default_model": "m-a"})).json()
    b = (await client.post(_BASE, json={"api_key": "sk-b-2222", "default_model": "m-b"})).json()

    r = await client.post(
        "/v1/users/me/llm-model-profiles",
        json={
            "name": "双槽",
            "main": {"origin": "byok", "provider_id": b["id"], "model": "m-b"},
            "background": {"origin": "byok", "provider_id": a["id"], "model": "m-a"},
            "set_as_default": True,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "双槽"
    assert body["main"]["provider_id"] == b["id"]
    assert body["background"]["provider_id"] == a["id"]
    assert body["is_default"] is True

    listed = (await client.get(_BASE)).json()
    assert listed["default_model_profile_id"] == body["id"]


async def test_model_profile_rejects_foreign_provider(client, byok):
    await register_and_login(client, "provuser9")
    await client.post(_BASE, json={"api_key": "sk-a-1111"})
    r = await client.post(
        "/v1/users/me/llm-model-profiles",
        json={
            "name": "坏",
            "main": {"origin": "byok", "provider_id": new_id(), "model": "m"},
        },
    )
    assert r.status_code == 422, r.text


# --- connectivity test (POST .../{id}/test), with the provider stubbed ---


class _FakeProvider:
    def __init__(
        self,
        *,
        fail: bool,
        supports_tools: bool | None = True,
        model_ids: list[str] | None = None,
    ) -> None:
        self._fail = fail
        self._supports_tools = supports_tools
        self._model_ids = model_ids if model_ids is not None else ["gpt-4o-mini"]
        self.probe_model: str | None = None
        self.list_models_called = False

    async def list_models(self) -> list[str]:
        self.list_models_called = True
        if self._fail:
            # Force fallback to probe so fail=True still exercises probe error path.
            from agentcore.core.errors import LLMError

            raise LLMError("list_models unavailable")
        return list(self._model_ids)

    async def probe(self, *, model: str) -> None:
        self.probe_model = model
        if self._fail:
            raise LLMError("API Key 无效或无权限（鉴权失败），请检查后重试")

    async def probe_tools(self, *, model: str) -> bool | None:
        return self._supports_tools

    async def close(self) -> None:
        pass


async def test_test_provider_active_on_success(client, byok, monkeypatch):
    await register_and_login(client, "provuser10")
    created = (
        await client.post(_BASE, json={"api_key": "sk-good-4242", "default_model": "gpt-4o-mini"})
    ).json()
    fake = _FakeProvider(fail=False, supports_tools=True)
    monkeypatch.setattr(
        "agentcore.llm.provider_service.build_provider",
        lambda creds, **kwargs: fake,
    )

    r = await client.post(f"{_BASE}/{created['id']}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active"
    assert body["supports_tools"] is True
    assert fake.list_models_called is True
    assert fake.probe_model is None  # /models success → skip probe

    persisted = (await client.get(_BASE)).json()["providers"][0]
    assert persisted["status"] == "active"
    assert persisted["supports_tools"] is True


async def test_test_provider_error_on_probe_failure(client, byok, monkeypatch):
    await register_and_login(client, "provuser11")
    created = (await client.post(_BASE, json={"api_key": "sk-bad-0000"})).json()
    monkeypatch.setattr(
        "agentcore.llm.provider_service.build_provider",
        lambda creds, **kwargs: _FakeProvider(fail=True),
    )
    r = await client.post(f"{_BASE}/{created['id']}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "error"
    assert body["message"]


async def test_test_missing_provider_returns_404(client, byok):
    await register_and_login(client, "provuser12")
    r = await client.post(f"{_BASE}/{new_id()}/test")
    assert r.status_code == 404, r.text


# --- deployment capability fields (moved onto the list response) ---


async def test_list_reports_platform_capability_dormant(client, byok, monkeypatch):
    """byok + key still present → platform_available false."""
    monkeypatch.setattr(settings, "platform_api_key", "sk-platform")
    await register_and_login(client, "provcap")
    body = (await client.get(_BASE)).json()
    assert body["platform_available"] is False
    assert body["platform_model"] is None
    assert body["billing_mode"] == "byok"


async def test_list_reports_platform_billing_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "billing_mode", "platform")
    monkeypatch.setattr(settings, "platform_api_key", "sk-platform")
    monkeypatch.setattr(settings, "encryption_key", _MASTER_KEY)
    await register_and_login(client, "provplat")
    body = (await client.get(_BASE)).json()
    assert body["providers"] == []
    assert body["billing_mode"] == "platform"
    assert body["platform_available"] is True


# --- billing preflight (route + gate) ---


async def test_send_message_refused_without_byok_provider(
    client, session_factory, byok
):
    user_id = await register_and_login(client, "provuser13")
    conv_id = await _make_conversation(session_factory, user_id=user_id)
    r = await client.post(
        f"/v1/conversations/{conv_id}/messages",
        json={"content": "hi", "delivery": "steer"},
    )
    assert r.status_code == 402, r.text
    assert r.json()["error"]["code"] == "LLM_KEY_REQUIRED"


async def test_preflight_byok_skips_quota_when_provider_present(
    client, session_factory, byok
):
    user_id = await register_and_login(client, "provuser14")
    conv_id = await _make_conversation(session_factory, user_id=user_id)
    await _seed_spend(
        session_factory, user_id=user_id, conversation_id=conv_id, total=_OVER_MONTHLY_NANO
    )
    await _add_provider(session_factory, user_id=user_id, api_key="sk-byok-user-1234")

    async with session_factory() as session:
        user = await UserRepository(session).get_by_id(user_id)
        result = await _preflight_turn_llm(
            session=session, user=user, cost_repo=CostEventRepository(session)
        )
    assert result.credentials is not None
    assert result.credentials.api_key == "sk-byok-user-1234"


async def test_preflight_tools_soft_gate_warning(client, session_factory, byok):
    user_id = await register_and_login(client, "provuser15")
    provider = await _add_provider(session_factory, user_id=user_id, api_key="sk-byok-tools")

    async with session_factory() as session:
        await UserLlmProviderRepository(session).update_supports_tools(provider.id, False)
        user = await UserRepository(session).get_by_id(user_id)
        result = await _preflight_turn_llm(
            session=session,
            user=user,
            cost_repo=CostEventRepository(session),
            needs_tools=True,
        )
        assert result.warnings == [TOOLS_SOFT_GATE_WARNING]
        plain = await _preflight_turn_llm(
            session=session,
            user=user,
            cost_repo=CostEventRepository(session),
            needs_tools=False,
        )
        assert plain.warnings == []


async def test_preflight_platform_enforces_quota(client, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "billing_mode", "platform")
    monkeypatch.setattr(settings, "platform_api_key", "sk-platform")
    user_id = await register_and_login(client, "provuser16")
    conv_id = await _make_conversation(session_factory, user_id=user_id)
    await _seed_spend(
        session_factory, user_id=user_id, conversation_id=conv_id, total=_OVER_MONTHLY_NANO
    )

    async with session_factory() as session:
        user = await UserRepository(session).get_by_id(user_id)
        with pytest.raises(QuotaExceededError):
            await _preflight_turn_llm(
                session=session, user=user, cost_repo=CostEventRepository(session)
            )


# --- migration equivalence: a single-provider user behaves like the old single-key ---


async def test_single_provider_user_equivalent_to_legacy_key(
    client, session_factory, byok
):
    """After migration a single-provider user resolves BYOK exactly as the old single-key
    user did: account default = that provider's model, turns run on its key."""
    user_id = await register_and_login(client, "provuser17")
    async with session_factory() as session:
        provider = await LlmProviderService(session).create_provider(
            user_id, label="DeepSeek", api_key="sk-solo-9999", default_model="deepseek-v4-pro"
        )

    # Catalog: current = the sole provider's model, tagged byok + that provider.
    r = await client.get("/v1/users/me/models")
    assert r.status_code == 200, r.text
    cat = r.json()
    assert cat["byok_configured"] is True
    assert cat["current"]["id"] == "deepseek-v4-pro"
    assert cat["current"]["origin"] == "byok"
    assert cat["current"]["provider_id"] == provider.id

    # Preflight (no conversation override) resolves to that provider's key, no quota gate.
    async with session_factory() as session:
        user = await UserRepository(session).get_by_id(user_id)
        result = await _preflight_turn_llm(
            session=session, user=user, cost_repo=CostEventRepository(session)
        )
    assert result.credentials is not None
    assert result.credentials.api_key == "sk-solo-9999"
    assert result.credentials.provider_id == provider.id
